import json
from typing import cast

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import (
    new_agent_parts_message,
    new_agent_text_message,
    new_task,
)
from a2a.utils.errors import ServerError

from ..agent.base_agent import BaseAgent


class ADKAgentExecutor(AgentExecutor):
    """Executor for ADK based agents."""

    def __init__(self, agent: BaseAgent):
        self.agent = agent

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if context.message is None:
            raise ServerError(
                error=UnsupportedOperationError(
                    message="ADKAgentExecutor requires a message in the context."
                )
            )
        query = context.get_user_input()
        task = context.current_task

        # This agent always produces Task objects. If this request does
        # not have current task, create a new one and use it.
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.contextId)
        # invoke the underlying agent, using streaming results. The streams
        # now are update events.
        async for item in self.agent.stream(query, task.contextId):
            is_task_complete = item["is_task_complete"]
            if not is_task_complete:
                await updater.update_status(
                    TaskState.working,
                    new_agent_text_message(item["updates"], task.contextId, task.id),
                )
                continue
            # If the response is a dictionary, assume its a form
            if isinstance(item["content"], dict):
                # Verify it is a valid form
                if (
                    "response" in item["content"]
                    and "result" in item["content"]["response"]
                ):
                    data = json.loads(cast(str, item["content"]["response"]["result"]))
                    await updater.update_status(
                        TaskState.input_required,
                        new_agent_parts_message(
                            [Part(root=DataPart(data=data))],
                            task.contextId,
                            task.id,
                        ),
                        final=True,
                    )
                    continue
                await updater.update_status(
                    TaskState.failed,
                    new_agent_text_message(
                        "Reaching an unexpected state",
                        task.contextId,
                        task.id,
                    ),
                    final=True,
                )
                break
            # Emit the appropriate events
            await updater.add_artifact(
                [Part(root=TextPart(text=item["content"]))], name="form"
            )
            await updater.complete()
            break

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())
