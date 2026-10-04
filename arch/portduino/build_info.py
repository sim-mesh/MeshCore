# What the Portduino boot banner says of the build: `git describe`, the commit
# and the build's UTC time, as MESHCORE_BUILD_DESCRIBE, MESHCORE_BUILD_COMMIT
# and MESHCORE_BUILD_DATE, given to PortduinoBoard.cpp alone so the rest of
# the build stays incremental.
import subprocess
import time

Import("env")


def git(*args):
    try:
        return subprocess.check_output(["git"] + list(args), cwd=env.subst("$PROJECT_DIR"),
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


DEFINES = [
    ("MESHCORE_BUILD_DESCRIBE", env.StringifyMacro(git("describe", "--tags", "--always", "--dirty"))),
    ("MESHCORE_BUILD_COMMIT", env.StringifyMacro(git("rev-parse", "--short=12", "HEAD"))),
    ("MESHCORE_BUILD_DATE", env.StringifyMacro(time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))),
]


def with_build_info(build_env, node):
    return build_env.Object(node, CPPDEFINES=list(build_env["CPPDEFINES"]) + DEFINES)


env.AddBuildMiddleware(with_build_info, "*/helpers/portduino/PortduinoBoard.cpp")
