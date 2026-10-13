from typing import TYPE_CHECKING

from nur.core.providers.ant import AntProvider
from nur.core.providers.cargo_make import CargoMakeProvider
from nur.core.providers.composer import ComposerProvider
from nur.core.providers.deno import DenoProvider
from nur.core.providers.grunt import GruntProvider
from nur.core.providers.gulp import GulpProvider
from nur.core.providers.invoke import InvokeProvider
from nur.core.providers.just import JustProvider
from nur.core.providers.make import MakeProvider
from nur.core.providers.mask import MaskProvider
from nur.core.providers.mise import MiseProvider
from nur.core.providers.moon import MoonProvider
from nur.core.providers.nox import NoxProvider
from nur.core.providers.npm import NpmProvider
from nur.core.providers.nx import NxProvider
from nur.core.providers.pdm import PdmProvider
from nur.core.providers.poe import PoeProvider
from nur.core.providers.pre_commit import PreCommitProvider
from nur.core.providers.rake import RakeProvider
from nur.core.providers.task import TaskfileProvider
from nur.core.providers.tox import ToxProvider
from nur.core.providers.turbo import TurboProvider
from nur.core.providers.vscode import VsCodeProvider
from nur.core.providers.workspace import WorkspaceProvider
from nur.core.providers.xc import XcProvider

if TYPE_CHECKING:
    from nur.core.models import Provider

PROVIDERS: list[Provider] = [
    NpmProvider(),
    DenoProvider(),
    ComposerProvider(),
    MakeProvider(),
    PdmProvider(),
    PoeProvider(),
    ToxProvider(),
    JustProvider(),
    TaskfileProvider(),
    PreCommitProvider(),
    MiseProvider(),
    CargoMakeProvider(),
    MoonProvider(),
    XcProvider(),
    VsCodeProvider(),
    NoxProvider(),
    MaskProvider(),
    InvokeProvider(),
    RakeProvider(),
    TurboProvider(),
    NxProvider(),
    GruntProvider(),
    GulpProvider(),
    AntProvider(),
    WorkspaceProvider(),
]

__all__ = [
    "PROVIDERS",
    "AntProvider",
    "CargoMakeProvider",
    "ComposerProvider",
    "DenoProvider",
    "GruntProvider",
    "GulpProvider",
    "InvokeProvider",
    "JustProvider",
    "MakeProvider",
    "MaskProvider",
    "MiseProvider",
    "MoonProvider",
    "NoxProvider",
    "NpmProvider",
    "NxProvider",
    "PdmProvider",
    "PoeProvider",
    "PreCommitProvider",
    "RakeProvider",
    "TaskfileProvider",
    "ToxProvider",
    "TurboProvider",
    "VsCodeProvider",
    "WorkspaceProvider",
    "XcProvider",
]
