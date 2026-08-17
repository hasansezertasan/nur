Usage
=====

``nur`` discovers the runnable tasks in your project (from ``Makefile``,
``package.json``, ``deno.json``/``deno.jsonc``, ``composer.json``, ``justfile``,
``Taskfile``, ``mise.toml``, ``Makefile.toml`` (cargo-make), ``moon.yml``, the xc
task section of ``README.md``, and the ``pdm``/``poe`` tables in
``pyproject.toml``) and runs them from one entry point.

Launch the TUI
--------------

Run ``nur`` with no arguments in a project directory to browse and run the
discovered tasks in an interactive terminal UI:

.. code-block:: sh

   nur

List the discovered tasks
-------------------------

.. code-block:: sh

   nur list

Tasks are grouped by their provider prefix (``make``, ``npm``, ``deno``,
``composer``, ``just``, ``task``, ``pdm``, ``poe``, ``mise``, ``cargo-make``,
``moon``, ``xc``).

Run a task
----------

Pass a task name. Use the bare name when it is unambiguous, or the
``prefix:name`` form to disambiguate across providers:

.. code-block:: sh

<<<<<<< before updating
   nur test
   nur make:test

Anything after ``--`` is forwarded verbatim to the underlying runner:

.. code-block:: sh

   nur test -- --verbose --fail-fast

Print the version
-----------------

.. code-block:: sh

   nur --version

As a library
------------

The package exposes its version for programmatic use:

.. code-block:: python

   import nur

   nur.__version__
=======
   nur interactive
>>>>>>> after updating
