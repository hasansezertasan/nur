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

Discovery (``nur.discovery``)
---------------------------------

Walks the working directory and builds the task registry from every supported
source file.

.. automodule:: nur.discovery

Registry (``nur.registry``)
-------------------------------

Holds discovered tasks and resolves a query to a single task, with ambiguity
and close-match handling.

.. automodule:: nur.registry

Execution (``nur.execution``)
---------------------------------

Runs a resolved task's command, propagating its exit code.

.. automodule:: nur.execution

Models (``nur.models``)
---------------------------

Core data types shared across discovery, the registry, and execution.

.. automodule:: nur.models

Providers (``nur.providers``)
---------------------------------

One module per supported task source (make, npm, just, taskfile, pdm, poe, mise,
cargo-make, xc).

.. automodule:: nur.providers.make

.. automodule:: nur.providers.npm

.. automodule:: nur.providers.just

.. automodule:: nur.providers.task

.. automodule:: nur.providers.pdm

.. automodule:: nur.providers.poe

.. automodule:: nur.providers.mise

.. automodule:: nur.providers.cargo_make

.. automodule:: nur.providers.xc

TUI (``nur.tui``)
---------------------------

Textual terminal user interface for browsing and running tasks.

.. automodule:: nur.tui.app
