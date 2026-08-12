.. A 7-character "=" underline (the length of "Modules") is treated as a
   merge-conflict separator by ``git diff --check`` / ``check-merge-conflict``;
   keep this underline longer than the title to avoid the false positive.

Modules
=========

An overview of the packages that make up ``nur``.
The API reference below is generated automatically from the source docstrings.

CLI (``nur.cli``)
---------------------------

Typer command-line interface: discovers project tasks and either runs one
directly or launches the TUI.

.. automodule:: nur.cli

Discovery (``nur.core.discovery``)
--------------------------------------

Walks the working directory and builds the task registry from every supported
source file.

.. automodule:: nur.core.discovery

Registry (``nur.core.registry``)
------------------------------------

Holds discovered tasks and resolves a query to a single task, with ambiguity
and close-match handling.

.. automodule:: nur.core.registry

Execution (``nur.core.execution``)
--------------------------------------

Runs a resolved task's command, propagating its exit code.

.. automodule:: nur.core.execution

Models (``nur.core.models``)
--------------------------------

Core data types shared across discovery, the registry, and execution.

.. automodule:: nur.core.models

Providers (``nur.core.providers``)
--------------------------------------

One module per supported task source (make, npm, deno, just, taskfile, pdm, poe,
mise, cargo-make, xc).

.. automodule:: nur.core.providers.make

.. automodule:: nur.core.providers.npm

.. automodule:: nur.core.providers.deno

.. automodule:: nur.core.providers.just

.. automodule:: nur.core.providers.task

.. automodule:: nur.core.providers.pdm

.. automodule:: nur.core.providers.poe

.. automodule:: nur.core.providers.mise

.. automodule:: nur.core.providers.cargo_make

.. automodule:: nur.core.providers.xc

TUI (``nur.tui``)
---------------------------

Textual terminal user interface for browsing and running tasks.

.. automodule:: nur.tui.app
