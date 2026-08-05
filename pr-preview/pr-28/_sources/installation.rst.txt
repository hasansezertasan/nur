Installation
============

``nur`` is an end-user command-line tool, not a library, so install it as a
standalone application rather than as a project dependency. It ships a single
``nur`` command that launches both the CLI and the TUI.

Stable release
--------------

Install ``nur`` into an isolated environment with your preferred tool installer:

.. code-block:: sh

   uv tool install nur

.. code-block:: sh

   pipx install nur

Or run it without installing:

.. code-block:: sh

   uvx nur

On macOS/Linux, install it from the
`Homebrew tap <https://github.com/hasansezertasan/homebrew-tap>`_:

.. code-block:: sh

   brew install hasansezertasan/tap/nur

On Windows, install it from the
`Scoop bucket <https://github.com/hasansezertasan/scoop-bucket>`_:

.. code-block:: sh

   scoop bucket add hasansezertasan https://github.com/hasansezertasan/scoop-bucket
   scoop install nur

From source
-----------

The source files for ``nur`` can be downloaded from the
`GitHub repo <https://github.com/hasansezertasan/nur>`_.

You can either clone the public repository:

.. code-block:: sh

   git clone https://github.com/hasansezertasan/nur.git

Or download the
`tarball <https://github.com/hasansezertasan/nur/tarball/main>`_:

.. code-block:: sh

   mkdir nur
   curl -fL https://github.com/hasansezertasan/nur/tarball/main | tar -xz --strip-components=1 -C nur

Once you have a copy of the source, you can install it with:

.. code-block:: sh

   cd nur
   uv pip install .
