import enum
import time

from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import Callable
from typing import Dict
from typing import FrozenSet
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from poetry.mixology import resolve_version
from poetry.mixology.failure import SolveFailure
from poetry.packages import DependencyPackage
from poetry.puzzle.exceptions import OverrideNeeded
from poetry.puzzle.exceptions import SolverProblemError
from poetry.puzzle.provider import Provider


if TYPE_CHECKING:
    from cleo.io.io import IO
    from poetry.core.packages.dependency import Dependency
    from poetry.core.packages.directory_dependency import DirectoryDependency
    from poetry.core.packages.file_dependency import FileDependency
    from poetry.core.packages.package import Package
    from poetry.core.packages.project_package import ProjectPackage
    from poetry.core.packages.url_dependency import URLDependency
    from poetry.core.packages.vcs_dependency import VCSDependency

    from poetry.puzzle.transaction import Transaction
    from poetry.repositories import Pool
    from poetry.repositories import Repository
    from poetry.utils.env import Env


class Solver:
    def __init__(
        self,
        package: "ProjectPackage",
        pool: "Pool",
        installed: "Repository",
        locked: "Repository",
        io: "IO",
        provider: Optional[Provider] = None,
    ):
        self._package = package
        self._pool = pool
        self._installed = installed
        self._locked = locked
        self._io = io

        if provider is None:
            provider = Provider(self._package, self._pool, self._io)

        self._provider = provider
        self._overrides: List[Dict] = []

    @property
    def provider(self) -> Provider:
        return self._provider

    @contextmanager
    def use_environment(self, env: "Env") -> Iterator[None]:
        with self.provider.use_environment(env):
            yield

    def solve(self, use_latest: List[str] = None) -> "Transaction":
        from poetry.puzzle.transaction import Transaction

        with self._provider.progress():
            start = time.time()
            packages, depths = self._solve(use_latest=use_latest)
            end = time.time()

            if len(self._overrides) > 1:
                self._provider.debug(
                    f"Complete version solving took {end - start:.3f} seconds with"
                    f" {len(self._overrides)} overrides"
                )
                self._provider.debug(
                    "Resolved with overrides:"
                    f" {', '.join(f'({b})' for b in self._overrides)}"
                )

        return Transaction(
            self._locked.packages,
            list(zip(packages, depths)),
            installed_packages=self._installed.packages,
            root_package=self._package,
        )

    def solve_in_compatibility_mode(
        self, overrides: Tuple[Dict, ...], use_latest: List[str] = None
    ) -> Tuple[List["Package"], List[int]]:

        packages = []
        depths = []
        for override in overrides:
            self._provider.debug(
                "<comment>Retrying dependency resolution "
                f"with the following overrides ({override}).</comment>"
            )
            self._provider.set_overrides(override)
            _packages, _depths = self._solve(use_latest=use_latest)
            for index, package in enumerate(_packages):
                if package not in packages:
                    packages.append(package)
                    depths.append(_depths[index])
                    continue
                else:
                    idx = packages.index(package)
                    pkg = packages[idx]
                    depths[idx] = max(depths[idx], _depths[index])

                    for dep in package.requires:
                        if dep not in pkg.requires:
                            pkg.add_dependency(dep)

        return packages, depths

    def _solve(self, use_latest: List[str] = None) -> Tuple[List["Package"], List[int]]:
        if self._provider._overrides:
            self._overrides.append(self._provider._overrides)

        locked = {
            package.name: DependencyPackage(package.to_dependency(), package)
            for package in self._locked.packages
        }

        try:
            result = resolve_version(
                self._package, self._provider, locked=locked, use_latest=use_latest
            )

            packages = result.packages
        except OverrideNeeded as e:
            return self.solve_in_compatibility_mode(e.overrides, use_latest=use_latest)
        except SolveFailure as e:
            raise SolverProblemError(e)

        # NOTE passing explicit empty array for seen to reset between invocations during
        # update + install cycle
        results = dict(
            depth_first_search(
                PackageNode(self._package, packages, seen=[]), aggregate_package_nodes
            )
        )

        # Merging feature packages with base packages
        final_packages = []
        depths = []
        for package in packages:
            if package.features:
                for _package in packages:
                    if (
                        _package.name == package.name
                        and not _package.is_same_package_as(package)
                        and _package.version == package.version
                    ):
                        for dep in package.requires:
                            if dep.is_same_package_as(_package):
                                continue

                            if dep not in _package.requires:
                                _package.add_dependency(dep)

                continue

            final_packages.append(package)
            depths.append(results[package])

        # Return the packages in their original order with associated depths
        return final_packages, depths


DFSNodeID = Tuple[str, FrozenSet[str], bool]


class DFSNode:
    def __init__(self, id: DFSNodeID, name: str, base_name: str) -> None:
        self.id = id
        self.name = name
        self.base_name = base_name

    def reachable(self) -> List:
        return []

    def visit(self, parents: List["PackageNode"]) -> None:
        pass

    def __str__(self) -> str:
        return str(self.id)


class VisitedState(enum.Enum):
    Unvisited = 0
    PartiallyVisited = 1
    Visited = 2


def depth_first_search(
    source: "PackageNode", aggregator: Callable
) -> List[Tuple["Package", int]]:
    back_edges: Dict[DFSNodeID, List["PackageNode"]] = defaultdict(list)
    visited: Dict[DFSNodeID, VisitedState] = {}
    topo_sorted_nodes: List["PackageNode"] = []

    dfs_visit(source, back_edges, visited, topo_sorted_nodes)

    # Combine the nodes by name
    combined_nodes = defaultdict(list)
    name_children = defaultdict(list)
    for node in topo_sorted_nodes:
        node.visit(back_edges[node.id])
        name_children[node.name].extend(node.reachable())
        combined_nodes[node.name].append(node)

    combined_topo_sorted_nodes = [
        combined_nodes.pop(node.name)
        for node in topo_sorted_nodes
        if node.name in combined_nodes
    ]

    return [
        aggregator(nodes, name_children[nodes[0].name])
        for nodes in combined_topo_sorted_nodes
    ]


def dfs_visit(
    node: "PackageNode",
    back_edges: Dict[DFSNodeID, List["PackageNode"]],
    visited: Dict[DFSNodeID, VisitedState],
    sorted_nodes: List["PackageNode"],
) -> bool:
    if visited.get(node.id, VisitedState.Unvisited) == VisitedState.Visited:
        return True
    if visited.get(node.id, VisitedState.Unvisited) == VisitedState.PartiallyVisited:
        # We have a circular dependency.
        # Since the dependencies are resolved we can
        # simply skip it because we already have it
        return True

    visited[node.id] = VisitedState.PartiallyVisited
    for neighbor in node.reachable():
        back_edges[neighbor.id].append(node)
        if not dfs_visit(neighbor, back_edges, visited, sorted_nodes):
            return False
    visited[node.id] = VisitedState.Visited
    sorted_nodes.insert(0, node)
    return True


class PackageNode(DFSNode):
    def __init__(
        self,
        package: "Package",
        packages: List["Package"],
        seen: List["Package"],
        previous: Optional["PackageNode"] = None,
        previous_dep: Optional[
            Union[
                "DirectoryDependency",
                "FileDependency",
                "URLDependency",
                "VCSDependency",
                "Dependency",
            ]
        ] = None,
        dep: Optional[
            Union[
                "DirectoryDependency",
                "FileDependency",
                "URLDependency",
                "VCSDependency",
                "Dependency",
            ]
        ] = None,
    ) -> None:
        self.package = package
        self.packages = packages
        self.seen = seen

        self.previous = previous
        self.previous_dep = previous_dep
        self.dep = dep
        self.depth = -1

        if not previous:
            self.category = "dev"
            self.groups: FrozenSet[str] = frozenset()
            self.optional = True
        elif dep:
            self.category = "main" if "default" in dep.groups else "dev"
            self.groups = dep.groups
            self.optional = dep.is_optional()
        else:
            raise ValueError("Both previous and dep must be passed")

        super().__init__(
            (package.complete_name, self.groups, self.optional),
            package.complete_name,
            package.name,
        )

    def reachable(self) -> List["PackageNode"]:
        children: List[PackageNode] = []

        # skip already traversed packages
        if self.package in self.seen:
            return []
        else:
            self.seen.append(self.package)

        if (
            self.dep
            and self.previous_dep
            and self.previous_dep is not self.dep
            and self.previous_dep.name == self.dep.name
        ):
            return []

        for dependency in self.package.all_requires:
            if self.previous and self.previous.name == dependency.name:
                # We have a circular dependency.
                # Since the dependencies are resolved we can
                # simply skip it because we already have it
                # N.B. this only catches cycles of length 2;
                # dependency cycles in general are handled by the DFS traversal
                continue

            for pkg in self.packages:
                if (
                    pkg.complete_name == dependency.complete_name
                    and (
                        dependency.constraint.allows(pkg.version)
                        or dependency.allows_prereleases()
                        and pkg.version.is_unstable()
                        and dependency.constraint.allows(pkg.version.stable)
                    )
                    and not any(
                        child.package.name == pkg.name
                        and child.groups == dependency.groups
                        for child in children
                    )
                ):
                    children.append(
                        PackageNode(
                            pkg,
                            self.packages,
                            self.seen,
                            self,
                            dependency,
                            self.dep or dependency,
                        )
                    )

        return children

    def visit(self, parents: List["PackageNode"]) -> None:
        # The root package, which has no parents, is defined as having depth -1
        # So that the root package's top-level dependencies have depth 0.
        self.depth = 1 + max(
            [
                parent.depth if parent.base_name != self.base_name else parent.depth - 1
                for parent in parents
            ]
            + [-2]
        )


def aggregate_package_nodes(
    nodes: List[PackageNode], children: List[PackageNode]
) -> Tuple["Package", int]:
    package = nodes[0].package
    depth = max(node.depth for node in nodes)
    groups: List[str] = []
    for node in nodes:
        groups.extend(node.groups)

    category = (
        "main" if any("default" in node.groups for node in children + nodes) else "dev"
    )
    optional = all(node.optional for node in children + nodes)
    for node in nodes:
        node.depth = depth
        node.category = category
        node.optional = optional

    package.category = category
    package.optional = optional

    return package, depth
