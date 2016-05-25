# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# Copyright 2016 Continuum Analytics, Inc.
#
# May be copied and distributed freely only as part of an Anaconda or
# Miniconda installation.
# -----------------------------------------------------------------------------

"""
Tests script.
"""

# Standard library imports
from __future__ import print_function
from os.path import dirname, realpath
import codecs
import cProfile
import os
import os.path as osp
import pstats
import re
import shutil
import subprocess
import sys

# Third party imports
from coverage.summary import SummaryReporter
from flake8.engine import get_style_guide
from pep257 import (
    run_pep257, NO_VIOLATIONS_RETURN_CODE, VIOLATIONS_RETURN_CODE,
    INVALID_OPTIONS_RETURN_CODE
    )
from pep257 import log as pep257_log
from pytest_cov.plugin import CoverageError

# Local imports
from ciotest.setup_atomic_replace import atomic_replace


HERE = dirname(realpath(__file__))
PY2 = sys.version_info[0] == 2

coding_utf8_header = u"# -*- coding: utf-8 -*-\n"
copyright_header = u"""
# -----------------------------------------------------------------------------
# Copyright 2016 Continuum Analytics, Inc.
#
# May be copied and distributed freely only as part of an Anaconda or
# Miniconda installation.
# -----------------------------------------------------------------------------
""".lstrip()

copyright_re = re.compile('# *Copyright ')

# -----------------------------------------------------------------------------
# --- Monkey patch coverage
# -----------------------------------------------------------------------------
try:
    # Attempt to force coverage to skip_covered, which pytest-cov does not
    # expose as an option (.coveragerc option is ignored by pytest-cov)
    original_init = SummaryReporter.__init__

    def modified_init(self, coverage, config):
        config.skip_covered = True
        original_init(self, coverage, config)

    SummaryReporter.__init__ = modified_init

    print("Coverage monkeypatched to skip_covered")
except Exception as e:
    print("Failed to monkeypatch coverage: {0}".format(str(e)),
          file=sys.stderr)


class Profiler(object):
    """
    """

    def __init__(self):
        self._profiler = cProfile.Profile()

    def __exit__(self, type, value, traceback):
        self._profiler.disable()
        ps = pstats.Stats(self._profiler,
                          stream=sys.stdout).sort_stats('cumulative')
        ps.print_stats()

    def __enter__(self):
        self._profiler.enable()


class Test(object):
    """
    """

    def __init__(self, root, module=None, format_only=False,
                 git_staged_only=False, profile_formatting=False,
                 pytestqt=False):

        self.root = root
        self.root_modules = [module]
        self.git_staged_only = git_staged_only
        self.format_only = format_only
        self.profile_formatting = profile_formatting
        self.pytestqt = pytestqt

        self._cpu_count = None
        self.failed = []
        self.pyfiles = None
        self.git_staged_pyfiles = None

        coverage_rc = os.path.join(root, ".coveragerc")
        cov = '--cov={0}'.format(module)
        coverage_args = ['--cov-config', coverage_rc, cov,
                         '--cov-report=term-missing', '--cov-report=html',
                         '--no-cov-on-fail']
        if PY2:
            # xdist appears to lock up the test suite with python2, maybe due
            # to an interaction with coverage
            enable_xdist = []
        else:
            enable_xdist = ['-n', str(self.cpu_count)]

        self.pytest_args = ['-rfew', '--durations=10'] + enable_xdist
        self.pytest_args = self.pytest_args + coverage_args

        self._clean()

    # --- Helpers
    # -------------------------------------------------------------------------
    def _clean(self):
        """
        """
        # Clean up leftover trash as best we can
        BUILD_TMP = os.path.join(self.root, 'build', 'tmp')
        if os.path.isdir(BUILD_TMP):
            print("Cleaning up {0}".format(BUILD_TMP))
            try:
                shutil.rmtree(BUILD_TMP, ignore_errors=True)
            except Exception as e:
                print("Failed to remove {0}: {1}".format(BUILD_TMP, str(e)))
            else:
                print("Done removing {0}".format(BUILD_TMP))

    @property
    def cpu_count(self):
        """
        """
        if self._cpu_count is None:
            try:
                import multiprocessing
                self._cpu_count = multiprocessing.cpu_count()
            except Exception:
                print("Using fallback CPU count", file=sys.stderr)
                self._cpu_count = 4
        return self._cpu_count

    def get_py_files(self):
        """
        """
        if self.pyfiles is None:
            pyfiles = []
            for root, dirs, files in os.walk(self.root):
                # Chop out hidden directories
                files = [f for f in files if not f[0] == '.']
                dirs[:] = [d for d in dirs if (d[0] != '.' and d != 'build' and
                                               d != '__pycache__')]
                # Now walk files
                for f in files:
                    if f.endswith(".py"):
                        pyfiles.append(os.path.join(root, f))
            self.pyfiles = pyfiles
        return self.pyfiles

    def get_git_staged_py_files(self):
        """
        """
        if self.git_staged_pyfiles is None:
            # --diff-filter=AM means "added" and "modified"
            # -z means nul-separated names
            out = subprocess.check_output(['git', 'diff', '--cached',
                                           '--name-only', '--diff-filter=AM',
                                           '-z'])
            git_changed = set(out.decode('utf-8').split('\x00'))
            git_changed.discard('')  # There's an empty line in git output
            git_changed = {osp.join(self.root, fname) for fname in git_changed}
            self.git_staged_pyfiles = [fname for fname in self.get_py_files()
                                       if fname in git_changed]

            print("Found {0} files: {1}".format(len(git_changed), git_changed))

        return self.git_staged_pyfiles

    def get_files(self):
        """
        """
        if self.git_staged_only:
            return self.get_git_staged_py_files()
        else:
            return self.get_py_files()

    def add_missing_init_py(self):
        """
        """
        for srcdir in self.root_modules:
            for root, dirs, files in os.walk(os.path.join(self.root, srcdir)):
                dirs[:] = [d for d in dirs if not (d[0] == '.' or
                                                   d == '__pycache__')]
                for d in dirs:
                    init_py = os.path.join(root, d, "__init__.py")
                    if not os.path.exists(init_py):
                        print("Creating {0}".format(init_py))
                        with codecs.open(init_py, 'w', 'utf-8') as handle:
                            handle.flush()

    def _add_headers(self, path):
        """
        """
        with codecs.open(path, 'r', 'utf-8') as f:
            old_contents = f.read()

        have_coding = (coding_utf8_header in old_contents)
        have_copyright = (copyright_re.search(old_contents) is not None)

        if have_coding and have_copyright:
            return

        if not have_coding:
            print("No encoding header comment in " + path)
            if "encoding_header" not in self.failed:
                self.failed.append("encoding_header")

        if not have_copyright:
            print("No copyright header comment in " + path)
            if "copyright_header" not in self.failed:
                self.failed.append("copyright_header")

        # Note: do NOT automatically change the copyright owner or
        # date.  The copyright owner/date is a statement of legal
        # reality, not a way to create legal reality. All we do
        # here is add an owner/date if there is none; if it's
        # incorrect, the person creating/reviewing the pull
        # request will need to fix it. If there's already an
        # owner/date then we leave it as-is assuming someone
        # has manually chosen it.
        contents = old_contents

        if not have_copyright:
            print("Adding copyright header to: " + path)
            contents = copyright_header + contents

        if not have_coding:
            print("Adding encoding header to: " + path)
            contents = coding_utf8_header + contents

        atomic_replace(path, contents, 'utf-8')

    def _start_format_files(self, paths):
        """
        """
        cmd = [sys.executable, os.path.join(HERE, 'setup_yapf_task.py')]
        env = os.environ.copy()
        env['CIOTEST_PROJECT_ROOT'] = self.root
        proc = subprocess.Popen(cmd + paths, env=env)
        return proc

    # --- Checks
    # -------------------------------------------------------------------------
    def check_headers(self):
        """
        """
        print("Checking file headers...")
        for pyfile in self.get_files():
            self._add_headers(pyfile)

    def check_yapf(self):
        """
        this uses some silly multi-process stuff because Yapf is
        very very slow and CPU-bound.
        Not using a multiprocessing because not sure how its "magic"
        (pickling, __main__ import) really works.
        """
        print("Formatting files...")
        print("{0} CPUs to run yapf processes".format(self.cpu_count))
        processes = []

        def await_one_process():
            if processes:
                # we pop(0) because the first process is the oldest
                proc = processes.pop(0)
                proc.wait()
                if proc.returncode != 0:
                    # we fail the tests if we reformat anything, because
                    # we want CI to complain if a PR didn't run yapf
                    if len(self.failed) == 0 or self.failed[-1] != 'yapf':
                        self.failed.append("yapf")

        def await_all_processes():
            while processes:
                await_one_process()

        def take_n(items, n):
            result = []
            while n > 0 and items:
                result.append(items.pop())
                n = n - 1
            return result

        all_files = list(self.get_files())
        while all_files:
            # we send a few files to each process to try to reduce
            # per-process setup time
            some_files = take_n(all_files, 3)
            processes.append(self._start_format_files(some_files))
            # Don't run too many at once, this is a goofy algorithm
            if len(processes) > (self.cpu_count * 3):
                while len(processes) > self.cpu_count:
                    await_one_process()

        assert [] == all_files
        await_all_processes()
        assert [] == processes

    def check_flake8(self):
        """
        """
        print("running flake8...")

        flake8_style = get_style_guide(paths=self.get_files())
        report = flake8_style.check_files()

        if report.total_errors > 0:
            print("{0} flake8 errors, see above to fix "
                  "them".format(str(report.total_errors)))
            self.failed.append('flake8')
        else:
            print("flake8 passed!")

    def check_pep257(self):
        """
        """
        print("running pep257...")

        # Hack pep257 not to spam enormous amounts of debug logging if you use
        # pytest -s. run_pep257() below calls log.setLevel
        def ignore_set_level(level):
            pass

        pep257_log.setLevel = ignore_set_level

        # Hack (replacing argv temporarily because pep257 looks at it)
        old_argv = sys.argv

        try:
            for module in self.root_modules:
                sys.argv = ['pep257', os.path.join(self.root, module)]
                code = run_pep257()
        finally:
            sys.argv = old_argv

        if code == INVALID_OPTIONS_RETURN_CODE:
            print("pep257 found invalid configuration.")
            self.failed.append('pep257')
        elif code == VIOLATIONS_RETURN_CODE:
            print("pep257 reported some violations.")
            self.failed.append('pep257')
        elif code == NO_VIOLATIONS_RETURN_CODE:
            print("pep257 says docstrings look good.")
        else:
            raise RuntimeError("unexpected code from pep257: "
                               "{0}".format(str(code)))

    def check_pytest(self):
        """
        """
        print("running pytest...")

        if self.pytestqt:
            try:
                import qtpy  # analysis:ignore
            except ImportError:
                pass
        import pytest

        try:
            errno = pytest.main(self.pytest_args)
            if errno != 0:
                print("pytest failed, code {errno}".format(errno=errno))
                self.failed.append('pytest')
        except CoverageError as e:
            print("Test coverage failure: {0}".format(str(e)))
            self.failed.append('pytest-coverage')

    def run(self):
        """
        """
        if self.git_staged_only:
            print("Only formatting {0} git-staged python files, skipping {1} "
                  "files".format(len(self.get_git_staged_py_files()),
                                 len(self.get_py_files())))

        self.add_missing_init_py()
        self.check_headers()

        # Only yapf is slow enough to really be worth profiling
        if self.profile_formatting:
            with Profiler():
                self.check_yapf()
        else:
            self.check_yapf()

        self.check_flake8()
        self.check_pep257()

        if not self.format_only:
            self.check_pytest()

        if os.path.exists(os.path.join(self.root, '.eggs')):
            print(".eggs directory exists which means some dependency was "
                  "not installed via conda/pip")
            print("  (if this happens on binstar, this may need fixing "
                  "in .binstar.yml)")
            print("  (if this happens on your workstation, try conda/pip "
                  "installing the deps and deleting .eggs")
            self.failed.append("eggs-directory-exists")

        if len(self.failed) > 0:
            print("Failures in: {0}".format(repr(self.failed)))
            sys.exit(1)
        else:
            if self.git_staged_only:
                print("Skipped some files (only checked {0} added/modified "
                      "files).".format(len(self.get_git_staged_py_files())))
            if self.format_only:
                print("Formatting looks good, but didn't run tests.")
            else:
                print("All tests passed!")