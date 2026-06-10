"""QP 评测确定性种子 DB(test_qp_eval_fixture 不变量 + test_qp_eval 真模型共用)。

布局(qp_eval.jsonl 的 must_contain 数字与此严格耦合,改这里必改 fixture):
- 场 1,2:无 slugline(防聚合误计)
- 场 3-6:江城家 日 内 | 场 7-10:江城家 夜 内 | 场 11-14:江城家 (时间未注明) 内 → 江城家共 12 场
- 场 15:街道 日 外;4 takes(pass/ng/tbd + 1 软删) → live 3
- 场 16:咖啡馆 夜 内;最新版剧本 4 角色(顾朗/沈舟/林晚/陈默)+ 舞台指示 + 台词含「合同」

raw_text 仅拼 text 列,非真实场记格式,不用于 raw_text 相关断言。
"""
from backend.db.dal import DAL

CHARACTERS_16 = ["顾朗", "沈舟", "林晚", "陈默"]

SCRIPT_16_LINES: list[tuple[str | None, str]] = [
    (None, "室内,咖啡馆,夜。四人围桌对峙,气氛紧张。"),
    ("顾朗", "这份合同你今天必须签。"),
    ("沈舟", "签了字,以后就别再来找我。"),
    ("林晚", "你们俩冷静点,先把话说清楚。"),
    ("陈默", "我只是来旁听的,别把我卷进去。"),
    ("顾朗", "没有退路了,公司等不起。"),
    ("沈舟", "那也轮不到你来逼我。"),
    ("林晚", "服务员快过来了,小声点。"),
]


def seed_qp_eval_db(dal: DAL) -> None:
    """幂等性不保证:只对全新空 DB 调用一次。"""
    if dal.list_scenes_readonly():
        raise RuntimeError("seed_qp_eval_db: DB 非空,只对空库调用一次")
    for code in ("1", "2"):
        dal.create_scene(code)
    for code in ("3", "4", "5", "6"):
        sid = dal.create_scene(code)
        dal.update_scene_heading(sid, int_ext="内", time_of_day="日", location="江城家")
    for code in ("7", "8", "9", "10"):
        sid = dal.create_scene(code)
        dal.update_scene_heading(sid, int_ext="内", time_of_day="夜", location="江城家")
    for code in ("11", "12", "13", "14"):
        sid = dal.create_scene(code)
        dal.update_scene_heading(sid, int_ext="内", location="江城家")  # 时间未注明

    sid15 = dal.create_scene("15")
    dal.update_scene_heading(sid15, int_ext="外", time_of_day="日", location="街道")
    t1, _ = dal.start_take(sid15, "1", 100.0)
    t2, _ = dal.start_take(sid15, "1", 200.0)
    t3, _ = dal.start_take(sid15, "1", 300.0)
    t4, _ = dal.start_take(sid15, "1", 400.0)
    dal.set_take_status(t1, "pass")
    dal.set_take_status(t2, "ng")
    dal.set_take_status(t3, "tbd")
    dal.delete_take(t4)  # 软删 → live 3

    sid16 = dal.create_scene("16")
    dal.update_scene_heading(sid16, int_ext="内", time_of_day="夜", location="咖啡馆")
    script_id = dal.insert_script(sid16, "\n".join(t for _, t in SCRIPT_16_LINES))
    for i, (character, text) in enumerate(SCRIPT_16_LINES, start=1):
        dal.insert_script_line(script_id, i, character, text)
