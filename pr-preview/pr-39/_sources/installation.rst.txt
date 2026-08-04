Installation
============

Possible extras:

- ``cli``: Installs typer and adds ``nur`` as a command.
- ``tui``: Installs textual and adds ``nur-tui`` as a command.
- ``all``: Installs all extras if available.

Stable release
--------------

To install ``nur``, run this command in your terminal:

.. code-block:: sh

   uv add nur

Or if you prefer to use ``pip``:

.. code-block:: sh

   pip install nur

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
