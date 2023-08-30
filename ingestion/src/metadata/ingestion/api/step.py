#  Copyright 2021 Collate
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""
Each of the ingestion steps: Source, Sink, Stage,...
"""
import traceback
from abc import ABC, abstractmethod
from typing import Iterable, Optional

from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.ingestion.api.closeable import Closeable
from metadata.ingestion.api.models import Either, Entity, StackTraceError
from metadata.ingestion.api.status import Status
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class WorkflowFatalError(Exception):
    """
    To be raised when we need to stop the workflow execution.
    E.g., during a failed Test Connection.
    Anything else will keep the workflow running.
    """


class Step(ABC, Closeable):
    """All Workflow steps must inherit this base class."""

    status: Status

    def __init__(self):
        self.status = Status()

    @classmethod
    @abstractmethod
    def create(
        cls, config_dict: dict, metadata_config: OpenMetadataConnection
    ) -> "Step":
        pass

    def get_status(self) -> Status:
        return self.status

    @abstractmethod
    def close(self) -> None:
        pass


class ReturnStep(Step, ABC):
    """Steps that run by returning a single unit"""

    @abstractmethod
    def _run(self, *args, **kwargs) -> Either:
        """
        Main entrypoint to execute the step
        """

    def run(self, *args, **kwargs) -> Optional[Entity]:
        """
        Run the step and handle the status and exceptions
        """
        try:
            result: Either = self._run(*args, **kwargs)
            if result.left is not None:
                self.status.failed(result.left)
                return None

            if result.right is not None:
                self.status.scanned(result.right)
                return result.right
        except WorkflowFatalError as err:
            logger.error(f"Fatal error running step [{self}]: [{err}]")
            raise err
        except Exception as exc:
            error = f"Unhandled exception during workflow processing: [{exc}]"
            logger.warning(error)
            self.status.failed(
                StackTraceError(
                    name="Unhandled", error=error, stack_trace=traceback.format_exc()
                )
            )

        return None


class StageStep(Step, ABC):
    """Steps that run by returning a single unit"""

    @abstractmethod
    def _run(self, *args, **kwargs) -> Iterable[Either[str]]:
        """
        Main entrypoint to execute the step.

        Note that the goal of this step is to store the
        processed data somewhere (e.g., a file). We will
        return an iterable to keep track of the processed
        entities / exceptions, but the next step (Bulk Sink)
        won't read these results. It will directly
        pick up the file components.
        """

    def run(self, *args, **kwargs) -> None:
        """
        Run the step and handle the status and exceptions.
        """
        try:
            for result in self._run(*args, **kwargs):
                if result.left is not None:
                    self.status.failed(result.left)

                if result.right is not None:
                    self.status.scanned(result.right)
        except WorkflowFatalError as err:
            logger.error(f"Fatal error running step [{self}]: [{err}]")
            raise err
        except Exception as exc:
            error = f"Unhandled exception during workflow processing: [{exc}]"
            logger.warning(error)
            self.status.failed(
                StackTraceError(
                    name="Unhandled", error=error, stack_trace=traceback.format_exc()
                )
            )


class IterStep(Step, ABC):
    """Steps that are run as Iterables"""

    @abstractmethod
    def _iter(self, *args, **kwargs) -> Iterable[Either]:
        """Main entrypoint to run through the Iterator"""

    def run(self) -> Iterable[Optional[Entity]]:
        """
        Run the step and handle the status and exceptions

        Note that we are overwriting the default run implementation
        in order to create a generator with `yield`.
        """
        try:
            for result in self._iter():
                if result.left is not None:
                    self.status.failed(result.left)
                    yield None

                if result.right is not None:
                    self.status.scanned(result.right)
                    yield result.right
        except WorkflowFatalError as err:
            logger.error(f"Fatal error running step [{self}]: [{err}]")
            raise err
        except Exception as exc:
            error = f"Encountered exception running step [{self}]: [{exc}]"
            logger.warning(error)
            self.status.failed(
                StackTraceError(
                    name="Unhandled", error=error, stack_trace=traceback.format_exc()
                )
            )


class BulkStep(Step, ABC):
    """
    Step that executes a single method, doing all
    the processing in bulk
    """

    @abstractmethod
    def run(self) -> None:
        pass