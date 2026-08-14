# tasks.py
import asyncio
import logging
from typing import Callable, Dict, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

# A dictionary to keep track of active tasks
tasks: Dict[str, asyncio.Task] = {}
chat_tasks = {}

# llens(#110): task 終了(cleanup)の listener 登録式 hook。サーバ駆動配達
# (utils/execution_resume.py)が登録し、名前空間 id で 2 種の wake を受ける:
# - 素の chat_id(chat ターンの終了)→ 静穏になった chat の未配達を配達
# - `exec:{chat_id}`(execution runner の終了)→ 完了直後の配達起点
# 登録式にするのは循環 import 回避のため(tasks.py は executions / middleware
# から import される最下層で、execution_resume を import できない)。
_cleanup_listeners: list[Callable[[Optional[str]], None]] = []


def register_cleanup_listener(listener: Callable[[Optional[str]], None]):
    """task 終了時に名前空間 id(create_task の id 引数)で呼ばれる同期 listener を
    登録する。イベントループの done callback 内で呼ばれるので、listener は
    ブロックしないこと(重い処理は自前で task 化する)。"""
    if listener not in _cleanup_listeners:
        _cleanup_listeners.append(listener)


def cleanup_task(task_id: str, id=None):
    """
    Remove a completed or canceled task from the global `tasks` dictionary.
    """
    tasks.pop(task_id, None)  # Remove the task if it exists

    # If an ID is provided, remove the task from the chat_tasks dictionary
    if id and task_id in chat_tasks.get(id, []):
        chat_tasks[id].remove(task_id)
        if not chat_tasks[id]:  # If no tasks left for this ID, remove the entry
            chat_tasks.pop(id, None)

    # llens(#110): 登録簿の掃除が終わってから通知する(listener が
    # list_task_ids_by_chat_id を読んだとき、終わった task はもう見えない)
    for listener in list(_cleanup_listeners):
        try:
            listener(id)
        except Exception:
            log.exception("task cleanup listener failed")


def create_task(coroutine, id=None):
    """
    Create a new asyncio task and add it to the global task dictionary.
    """
    task_id = str(uuid4())  # Generate a unique ID for the task
    task = asyncio.create_task(coroutine)  # Create the task

    # Add a done callback for cleanup
    task.add_done_callback(lambda t: cleanup_task(task_id, id))
    tasks[task_id] = task

    # If an ID is provided, associate the task with that ID
    if chat_tasks.get(id):
        chat_tasks[id].append(task_id)
    else:
        chat_tasks[id] = [task_id]

    return task_id, task


def get_task(task_id: str):
    """
    Retrieve a task by its task ID.
    """
    return tasks.get(task_id)


def list_tasks():
    """
    List all currently active task IDs.
    """
    return list(tasks.keys())


def list_task_ids_by_chat_id(id):
    """
    List all tasks associated with a specific ID.
    """
    return chat_tasks.get(id, [])


async def stop_task(task_id: str):
    """
    Cancel a running task and remove it from the global task list.
    """
    task = tasks.get(task_id)
    if not task:
        raise ValueError(f"Task with ID {task_id} not found.")

    task.cancel()  # Request task cancellation
    try:
        await task  # Wait for the task to handle the cancellation
    except asyncio.CancelledError:
        pass
    # llens: 応答生成タスクは CancelledError を内部で処理して正常終了する
    # (中断時の保存・イベント順序が仕事のうち)ため、「cancel 後に完走した」も成功。
    tasks.pop(task_id, None)
    return {"status": True, "message": f"Task {task_id} successfully stopped."}
