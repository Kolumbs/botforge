"""Framework-neutral tool descriptors.

``ToolSpec`` is the seam between botforge's own tool layer and whichever agent
framework executes the loop. Nothing here imports an LLM SDK, so the modules
that define and store tools stay independent of the framework; binding a spec
to a concrete framework is the adapter's job (see ``openai_tools.py``).
"""


class ToolSpec:
    """A tool definition, independent of any agent framework.

    :param name: the name the model calls the tool by.
    :param description: what the tool does, shown to the model.
    :param params: a ``pydantic.BaseModel`` subclass describing the arguments.
    :param handler: an async callable ``handler(ctx, params) -> str``.
    """

    def __init__(self, name, description, params, handler):
        self.name = name
        self.description = description
        self.params = params
        self.handler = handler
