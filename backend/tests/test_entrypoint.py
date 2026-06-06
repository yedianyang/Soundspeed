"""build_app 启动逻辑测试。"""

from backend.core.session import SessionState
from backend.db.dal import DAL


def test_restore_active_scene_sets_session(tmp_dal: DAL) -> None:
    """启动恢复：DB 有活跃场 → session.scene_id 被设上（重启后 NP 有上下文，不再静默空上下文）。"""
    from backend.api.entrypoint import restore_active_scene  # noqa: PLC0415

    scene_id = tmp_dal.create_scene("scene_restore")
    tmp_dal.set_active_scene(scene_id)
    session = SessionState()
    assert session.scene_id is None

    restore_active_scene(tmp_dal, session)
    assert session.scene_id == scene_id


def test_restore_active_scene_no_active_is_noop(tmp_dal: DAL) -> None:
    """DB 无活跃场（空库）→ session.scene_id 保持 None，不乱设。"""
    from backend.api.entrypoint import restore_active_scene  # noqa: PLC0415

    session = SessionState()
    restore_active_scene(tmp_dal, session)
    assert session.scene_id is None


def test_restore_active_scene_does_not_restore_take(tmp_dal: DAL) -> None:
    """只恢复活跃场，不恢复活跃 take——录制进程已随重启消失，take_active 必须 False。"""
    from backend.api.entrypoint import restore_active_scene  # noqa: PLC0415

    scene_id = tmp_dal.create_scene("scene_restore2")
    tmp_dal.set_active_scene(scene_id)
    session = SessionState()

    restore_active_scene(tmp_dal, session)
    assert session.scene_id == scene_id
    assert session.take_active is False
    assert session.take_id is None
