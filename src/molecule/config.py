#  Copyright (c) 2015-2018 Cisco Systems, Inc.
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to
#  deal in the Software without restriction, including without limitation the
#  rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
#  sell copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#  FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
#  DEALINGS IN THE SOFTWARE.
"""Config Module."""

import copy
import logging
import os
import warnings

from collections.abc import MutableMapping
from pathlib import Path
from uuid import uuid4

from ansible_compat.ports import cache, cached_property
from packaging.version import Version

from molecule import api, interpolation, platforms, scenario, state, util
from molecule.app import app
from molecule.data import __file__ as data_module
from molecule.dependency import ansible_galaxy, shell
from molecule.model import schema_v3
from molecule.provisioner import ansible
from molecule.util import boolean


LOG = logging.getLogger(__name__)
MOLECULE_DEBUG = boolean(os.environ.get("MOLECULE_DEBUG", "False"))
MOLECULE_VERBOSITY = int(os.environ.get("MOLECULE_VERBOSITY", 0))
MOLECULE_DIRECTORY = "molecule"
MOLECULE_FILE = "molecule.yml"
MOLECULE_KEEP_STRING = "MOLECULE_"
DEFAULT_DRIVER = "default"

MOLECULE_EMBEDDED_DATA_DIR = os.path.dirname(data_module)  # noqa: PTH120


@cache
def ansible_version() -> Version:
    """Retrieve Ansible version."""
    warnings.warn(  # noqa: B028
        "molecule.config.ansible_version is deprecated, will be removed in the future.",
        category=DeprecationWarning,
    )
    return app.runtime.version


# https://stackoverflow.com/questions/16017397/injecting-function-call-after-init-with-decorator
class NewInitCaller(type):
    """NewInitCaller."""

    def __call__(cls, *args, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ANN002, ANN003, ANN101, ANN204, D102
        obj = type.__call__(cls, *args, **kwargs)
        obj.after_init()
        return obj


class Config(metaclass=NewInitCaller):
    """Config Class.

    Molecule searches the current directory for ``molecule.yml`` files by
    globbing `molecule/*/molecule.yml`.  The files are instantiated into
    a list of Molecule [molecule.config.Config][] objects, and each Molecule subcommand
    operates on this list.

    The directory in which the ``molecule.yml`` resides is the Scenario's
    directory.  Molecule performs most functions within this directory.

    The [molecule.config.Config][] object instantiates Dependency, Driver,
    Platforms, Provisioner, Verifier_,
    [scenario][], and State_ references.
    """

    # pylint: disable=too-many-instance-attributes
    # Config objects should be allowed to have any number of attributes
    def __init__(  # type: ignore[no-untyped-def]
        self,  # noqa: ANN101
        molecule_file: str,  # pylint: disable=redefined-outer-name
        args={},  # noqa: ANN001, B006
        command_args={},  # noqa: ANN001, B006
        ansible_args=(),  # noqa: ANN001
    ) -> None:
        """Initialize a new config class and returns None.

        :param molecule_file: A string containing the path to the Molecule file
         to be parsed.
        :param args: An optional dict of options, arguments and commands from
         the CLI.
        :param command_args: An optional dict of options passed to the
         subcommand from the CLI.
        :param ansible_args: An optional tuple of arguments provided to the
         ``ansible-playbook`` command.
        :returns: None
        """
        self.molecule_file = molecule_file
        self.args = args
        self.command_args = command_args
        self.ansible_args = ansible_args
        self.config = self._get_config()
        self._action = None
        self._run_uuid = str(uuid4())
        self.project_directory = os.getenv(
            "MOLECULE_PROJECT_DIRECTORY",
            os.getcwd(),  # noqa: PTH109
        )
        self.runtime = app.runtime
        self.scenario_path = Path(molecule_file).parent

    def after_init(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        self.config = self._reget_config()  # type: ignore[no-untyped-call]
        if self.molecule_file:
            self._validate()  # type: ignore[no-untyped-call]

    def write(self) -> None:  # noqa: ANN101, D102
        util.write_file(self.config_file, util.safe_dump(self.config))

    @property
    def ansible_collections_path(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201
        """Return collection path variable for current version of Ansible."""
        # https://github.com/ansible/ansible/pull/70007
        if self.runtime.version >= Version("2.10.0.dev0"):
            return "ANSIBLE_COLLECTIONS_PATH"
        return "ANSIBLE_COLLECTIONS_PATHS"

    @property
    def config_file(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return os.path.join(self.scenario.ephemeral_directory, MOLECULE_FILE)  # noqa: PTH118

    @property
    def is_parallel(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return self.command_args.get("parallel", False)

    @property
    def platform_name(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return self.command_args.get("platform_name", None)

    @property
    def debug(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return self.args.get("debug", MOLECULE_DEBUG)

    @property
    def env_file(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return util.abs_path(self.args.get("env_file"))

    @property
    def subcommand(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return self.command_args["subcommand"]

    @property
    def action(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return self._action

    @action.setter
    def action(self, value):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN101, ANN202
        self._action = value

    @property
    def cache_directory(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return "molecule_parallel" if self.is_parallel else "molecule"

    @property
    def molecule_directory(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return molecule_directory(self.project_directory)

    @cached_property
    def dependency(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        dependency_name = self.config["dependency"]["name"]
        if dependency_name == "galaxy":
            return ansible_galaxy.AnsibleGalaxy(self)
        if dependency_name == "shell":
            return shell.Shell(self)
        return None

    @cached_property
    def driver(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        driver_name = self._get_driver_name()  # type: ignore[no-untyped-call]
        driver = None

        api_drivers = api.drivers(config=self)
        if driver_name not in api_drivers:
            msg = f"Failed to find driver {driver_name}. Please ensure that the driver is correctly installed."  # noqa: E501
            util.sysexit_with_message(msg)

        driver = api_drivers[driver_name]
        driver.name = driver_name

        return driver

    @property
    def env(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return {
            "MOLECULE_DEBUG": str(self.debug),
            "MOLECULE_FILE": self.config_file,
            "MOLECULE_ENV_FILE": str(self.env_file),
            "MOLECULE_STATE_FILE": self.state.state_file,
            "MOLECULE_INVENTORY_FILE": self.provisioner.inventory_file,
            "MOLECULE_EPHEMERAL_DIRECTORY": self.scenario.ephemeral_directory,
            "MOLECULE_SCENARIO_DIRECTORY": self.scenario.directory,
            "MOLECULE_PROJECT_DIRECTORY": self.project_directory,
            "MOLECULE_INSTANCE_CONFIG": self.driver.instance_config,
            "MOLECULE_DEPENDENCY_NAME": self.dependency.name,
            "MOLECULE_DRIVER_NAME": self.driver.name,
            "MOLECULE_PROVISIONER_NAME": self.provisioner.name,
            "MOLECULE_SCENARIO_NAME": self.scenario.name,
            "MOLECULE_VERIFIER_NAME": self.verifier.name,
            "MOLECULE_VERIFIER_TEST_DIRECTORY": self.verifier.directory,
        }

    @cached_property
    def platforms(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return platforms.Platforms(
            self,
            parallelize_platforms=self.is_parallel,
            platform_name=self.platform_name,
        )

    @cached_property
    def provisioner(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        provisioner_name = self.config["provisioner"]["name"]
        if provisioner_name == "ansible":
            return ansible.Ansible(self)
        return None

    @cached_property
    def scenario(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return scenario.Scenario(self)

    @cached_property
    def state(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        myState = state.State(self)  # noqa: N806
        # look at state file for molecule.yml date modified and warn if they do not match
        if self.molecule_file and os.path.isfile(self.molecule_file):  # noqa: PTH113
            modTime = os.path.getmtime(self.molecule_file)  # noqa: PTH204, N806
            if myState.molecule_yml_date_modified is None:
                myState.change_state("molecule_yml_date_modified", modTime)
            elif myState.molecule_yml_date_modified != modTime:
                LOG.warning(
                    "The scenario config file ('%s') has been modified since the scenario was created. "  # noqa: E501
                    "If recent changes are important, reset the scenario with 'molecule destroy' to clean up created items or "  # noqa: E501
                    "'molecule reset' to clear current configuration.",
                    self.molecule_file,
                )

        return state.State(self)

    @cached_property
    def verifier(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN201, D102
        return api.verifiers(self).get(self.config["verifier"]["name"], None)  # type: ignore[no-untyped-call]

    def _get_driver_name(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN202
        # the state file contains the driver from the last run
        driver_from_state_file = self.state.driver
        # the user may supply a driver on the command line
        driver_from_cli = self.command_args.get("driver_name")
        # the driver may also be edited in the scenario
        driver_from_scenario = self.config["driver"]["name"]

        if driver_from_state_file:
            driver_name = driver_from_state_file
        elif driver_from_cli:
            driver_name = driver_from_cli
        else:
            driver_name = driver_from_scenario

        if driver_from_cli and (driver_from_cli != driver_name):
            msg = (
                f"Instance(s) were created with the '{driver_name}' driver, but the "
                f"subcommand is using '{driver_from_cli}' driver."
            )
            util.sysexit_with_message(msg)

        if driver_from_state_file and driver_name not in api.drivers():
            msg = (
                f"Driver '{driver_name}' from state-file "
                f"'{self.state.state_file}' is not available."
            )
            util.sysexit_with_message(msg)

        if driver_from_scenario != driver_name:
            msg = (
                f"Driver '{driver_name}' is currently in use but the scenario config "
                f"has changed and now defines '{driver_from_scenario}'. "
                "To change drivers, run 'molecule destroy' for converged scenarios or 'molecule reset' otherwise."  # noqa: E501
            )
            LOG.warning(msg)

        return driver_name

    def _get_config(self) -> MutableMapping:  # type: ignore[type-arg]  # noqa: ANN101
        """Perform a prioritized recursive merge of config files.

        Returns a new dict.  Prior to merging the config files are interpolated with
        environment variables.

        :return: dict
        """
        return self._combine(keep_string=MOLECULE_KEEP_STRING)

    def _reget_config(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN202
        """Perform the same prioritized recursive merge from `get_config`.

        Interpolates the ``keep_string`` left behind in the original
        ``get_config`` call.  This is probably __very__ bad.

        :return: dict
        """
        env = util.merge_dicts(os.environ, self.env)
        env = set_env_from_file(env, self.env_file)

        return self._combine(env=env)

    def _combine(self, env=os.environ, keep_string=None) -> MutableMapping:  # type: ignore[no-untyped-def, type-arg]  # noqa: ANN001, ANN101
        """Perform a prioritized recursive merge of config files.

        Returns a new dict.  Prior to merging the config files are interpolated with
        environment variables.

        1. Loads Molecule defaults.
        2. Loads a base config (if provided) and merges on top of defaults.
        3. Loads the scenario's ``molecule file`` and merges on top of previous
           merge.

        :return: dict
        """
        defaults = self._get_defaults()
        base_configs = filter(os.path.exists, self.args.get("base_config", []))
        for base_config in base_configs:
            with open(base_config) as stream:  # noqa: PTH123
                s = stream.read()
                interpolated_config = self._interpolate(s, env, keep_string)
                defaults = util.merge_dicts(
                    defaults,
                    util.safe_load(interpolated_config),
                )

        if self.molecule_file:
            with open(self.molecule_file) as stream:  # noqa: PTH123
                s = stream.read()
                interpolated_config = self._interpolate(s, env, keep_string)
                defaults = util.merge_dicts(
                    defaults,
                    util.safe_load(interpolated_config),
                )

        return defaults

    def _interpolate(self, stream: str, env: MutableMapping, keep_string: str) -> str:  # type: ignore[type-arg]  # noqa: ANN101
        env = set_env_from_file(env, self.env_file)

        i = interpolation.Interpolator(interpolation.TemplateWithDefaults, env)

        try:
            return i.interpolate(stream, keep_string)
        except interpolation.InvalidInterpolation as e:
            msg = f"parsing config file '{self.molecule_file}'.\n\n{e.place}\n{e.string}"
            util.sysexit_with_message(msg)
        return ""

    def _get_defaults(self) -> MutableMapping:  # type: ignore[type-arg]  # noqa: ANN101
        if not self.molecule_file:
            scenario_name = "default"
        else:
            scenario_name = (
                os.path.basename(os.path.dirname(self.molecule_file))  # noqa: PTH119, PTH120
                or "default"
            )
        return {
            "dependency": {
                "name": "galaxy",
                "command": None,
                "enabled": True,
                "options": {},
                "env": {},
            },
            "driver": {
                "name": "default",
                "provider": {"name": None},
                "options": {"managed": True},
                "ssh_connection_options": [],
                "safe_files": [],
            },
            "platforms": [],
            "prerun": True,
            "role_name_check": 0,
            "provisioner": {
                "name": "ansible",
                "config_options": {},
                "ansible_args": [],
                "connection_options": {},
                "options": {},
                "env": {},
                "inventory": {
                    "hosts": {},
                    "host_vars": {},
                    "group_vars": {},
                    "links": {},
                },
                "children": {},
                "playbooks": {
                    "cleanup": "cleanup.yml",
                    "create": "create.yml",
                    "converge": "converge.yml",
                    "destroy": "destroy.yml",
                    "prepare": "prepare.yml",
                    "side_effect": "side_effect.yml",
                    "verify": "verify.yml",
                },
                "log": True,
            },
            "scenario": {
                "name": scenario_name,
                "check_sequence": [
                    "dependency",
                    "cleanup",
                    "destroy",
                    "create",
                    "prepare",
                    "converge",
                    "check",
                    "cleanup",
                    "destroy",
                ],
                "cleanup_sequence": ["cleanup"],
                "converge_sequence": ["dependency", "create", "prepare", "converge"],
                "create_sequence": ["dependency", "create", "prepare"],
                "destroy_sequence": ["dependency", "cleanup", "destroy"],
                "test_sequence": [
                    # dependency must be kept before lint to avoid errors
                    "dependency",
                    "cleanup",
                    "destroy",
                    "syntax",
                    "create",
                    "prepare",
                    "converge",
                    "idempotence",
                    "side_effect",
                    "verify",
                    "cleanup",
                    "destroy",
                ],
            },
            "verifier": {
                "name": "ansible",
                "enabled": True,
                "options": {},
                "env": {},
                "additional_files_or_dirs": [],
            },
        }

    def _validate(self):  # type: ignore[no-untyped-def]  # noqa: ANN101, ANN202
        msg = f"Validating schema {self.molecule_file}."
        LOG.debug(msg)

        errors = schema_v3.validate(self.config)  # type: ignore[no-untyped-call]
        if errors:
            msg = f"Failed to validate {self.molecule_file}\n\n{errors}"
            util.sysexit_with_message(msg)


def molecule_directory(path: str) -> str:
    """Return directory of the current scenario."""
    return os.path.join(path, MOLECULE_DIRECTORY)  # noqa: PTH118


def molecule_file(path: str) -> str:
    """Return file path of current scenario."""
    return os.path.join(path, MOLECULE_FILE)  # noqa: PTH118


def set_env_from_file(env: MutableMapping[str, str], env_file: str) -> MutableMapping:  # type: ignore[type-arg]
    """Load environment from file."""
    if env_file and os.path.exists(env_file):  # noqa: PTH110
        env = copy.copy(env)
        d = util.safe_load_file(env_file)
        for k, v in d.items():
            env[k] = v

        return env

    return env
