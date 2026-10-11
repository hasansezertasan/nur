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

Verify release provenance
-------------------------

The release workflow uses two complementary attestation mechanisms:

* GitHub build attestations record SLSA provenance for distributions when they
  are built, including workflow and source information. Their Sigstore bundles
  are also attached to the GitHub Release.
* PyPI publication attestations identify the Trusted Publisher that uploaded each
  distribution. The publishing action enables these by default for Trusted
  Publishing to PyPI or TestPyPI. They are available through PyPI's per-file
  provenance APIs and UI, rather than as GitHub release bundles.

The publication claim does not replace the structured build-provenance claim.
Both refer to the distribution bytes transferred from the build job, without an
intervening rebuild. See `PyPI's attestation documentation
<https://docs.pypi.org/attestations/>`_ for the index-hosted publication claim.

After downloading a wheel or source distribution, verify its GitHub build
attestation against this repository's release workflow and ``main`` ref:

.. code-block:: sh

   gh attestation verify <downloaded-distribution> \
     --repo hasansezertasan/nur \
     --signer-workflow hasansezertasan/nur/.github/workflows/release.yml \
     --source-ref refs/heads/main

To use the matching provenance bundle downloaded from the GitHub Release instead
of fetching the attestation from GitHub's API, add ``--bundle``:

.. code-block:: sh

   gh attestation verify <downloaded-distribution> \
     --bundle <downloaded-provenance-bundle> \
     --repo hasansezertasan/nur \
     --signer-workflow hasansezertasan/nur/.github/workflows/release.yml \
     --source-ref refs/heads/main

Fully offline verification also needs trusted-root material; see
`GitHub's offline verification guide
<https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/verify-attestations-offline>`_.

GitHub build attestations are available for public repositories on current
GitHub plans. Private and internal repositories require GitHub Enterprise Cloud
and the repository variable ``ENABLE_PRIVATE_ATTESTATIONS=true``. This opt-in
applies to GitHub build attestations, not PyPI's publication attestations. PyPI's
default attestation path requires Trusted Publishing and uses public Sigstore
infrastructure; publication with an API token or an explicit attestation opt-out
does not generate those attestations automatically.

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
   uv tool install .
