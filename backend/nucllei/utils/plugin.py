import os
import sys
import types
import tempfile
import logging

from nucllei.env import SRC_LOG_LEVELS
from nucllei.models.functions import Functions
from nucllei.models.tools import Tools

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


# llens(#44/#45): frontmatter(title/author/version/requirements)機構は tools・functions
# とも退役。title/description の真実源は DB の name/meta.description のみ、要件は事前導入
# 前提(air-gapped)で runtime pip も廃止。旧 extract_frontmatter /
# install_frontmatter_requirements は完全 dead のため削除した(#45 でまとめて掃除)。


def replace_imports(content):
    """
    Replace the import paths in the content.
    """
    replacements = {
        "from utils": "from nucllei.utils",
        "from apps": "from nucllei.apps",
        "from main": "from nucllei.main",
        "from config": "from nucllei.config",
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    return content


def load_tool_module_by_id(tool_id, content=None):
    # llens(#42): 組み込みツールは repo モジュールから返す(DB content は使わない)。
    # 循環 import 回避のため遅延 import
    from nucllei.builtin_tools import is_builtin_tool_id, load_builtin_tool

    if is_builtin_tool_id(tool_id):
        return load_builtin_tool(tool_id)

    if content is None:
        tool = Tools.get_tool_by_id(tool_id)
        if not tool:
            raise Exception(f"Toolkit not found: {tool_id}")

        content = tool.content

        content = replace_imports(content)
        Tools.update_tool_by_id(tool_id, {"content": content})
    # llens(#44): tool の frontmatter は退役。title/description の真実源は DB の
    # name/meta.description のみとし、requirements による tool ロード時 pip も廃止
    # (air-gapped、依存は事前導入前提)。

    module_name = f"tool_{tool_id}"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module

    # Create a temporary file and use it to define `__file__` so
    # that it works as expected from the module's perspective.
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()
    try:
        with open(temp_file.name, "w", encoding="utf-8") as f:
            f.write(content)
        module.__dict__["__file__"] = temp_file.name

        # Executing the modified content in the created module's namespace
        exec(content, module.__dict__)
        log.info(f"Loaded module: {module.__name__}")

        # Create and return the object if the class 'Tools' is found in the module
        # llens(#44): frontmatter は退役。第2要素は builtin と同じく空 dict で維持
        # (load_function_module_by_id との外形対称を保つ)。
        if hasattr(module, "Tools"):
            return module.Tools(), {}
        else:
            raise Exception("No Tools class found in the module")
    except Exception as e:
        log.error(f"Error loading module: {tool_id}: {e}")
        del sys.modules[module_name]  # Clean up
        raise e
    finally:
        os.unlink(temp_file.name)


def load_function_module_by_id(function_id, content=None):
    if content is None:
        function = Functions.get_function_by_id(function_id)
        if not function:
            raise Exception(f"Function not found: {function_id}")
        content = function.content

        content = replace_imports(content)
        Functions.update_function_by_id(function_id, {"content": content})
    # llens(#45): tool 経路(#44)と同じく frontmatter/pip は退役。requirements の
    # runtime インストールはしない(air-gapped、依存は事前導入前提)。

    module_name = f"function_{function_id}"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module

    # Create a temporary file and use it to define `__file__` so
    # that it works as expected from the module's perspective.
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()
    try:
        with open(temp_file.name, "w", encoding="utf-8") as f:
            f.write(content)
        module.__dict__["__file__"] = temp_file.name

        # Execute the modified content in the created module's namespace
        exec(content, module.__dict__)
        log.info(f"Loaded module: {module.__name__}")

        # Create appropriate object based on available class type in the module.
        # llens(#45): 第3要素(旧 frontmatter)は tool 経路と同じく空 dict で外形対称を保つ。
        if hasattr(module, "Pipe"):
            return module.Pipe(), "pipe", {}
        elif hasattr(module, "Filter"):
            return module.Filter(), "filter", {}
        elif hasattr(module, "Action"):
            return module.Action(), "action", {}
        elif hasattr(module, "Attachment"):
            # llens(#52): アップロード時に発火する Attachment 種別。
            return module.Attachment(), "attachment", {}
        else:
            raise Exception("No Function class found in the module")
    except Exception as e:
        log.error(f"Error loading module: {function_id}: {e}")
        del sys.modules[module_name]  # Cleanup by removing the module in case of error
        # llens(#46): is_active 撤去に伴い、ロード失敗時の自動無効化はしない
        # (壊れた function は割り当て解除 or 修正で対処)。
        raise e
    finally:
        os.unlink(temp_file.name)
