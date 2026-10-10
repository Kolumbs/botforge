"""botforge: a chatbot platform whose personality and tools are database content.

This module deliberately imports nothing. The zoozl plugin lives in
``botforge.plugin`` and is the only part that needs the agent framework and the
transport layer, so point zoozl at it directly::

    extensions = ["botforge.plugin"]

Keeping the package root empty means ``botforge.tools``, ``botforge.dynamic_tools``
and ``botforge.session`` can be imported - and tested - without an LLM SDK
installed.
"""
