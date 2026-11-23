"""生成实验室规模语料(36 店连锁手册 + 现存链路文档)与配套题集/负例集。

确定性生成(无随机):门店参数表驱动文档正文与 gold,保证片段逐字一致。
用途:在合成语料饱和(召回恒 1.000)之外提供一份"近似干扰密度"接近真实业务
的评测语料;真实租户语料到位后按同一 dataset 形态替换,无需改评测代码。
两个形态:`--variant context`(缺省,正文含店名)与 `--variant stripped`
(正文剥离店名,v1 缺陷形态,作为块索引上下文的判别语料)。
运行:`uv run python evals/retrieval/generate_scale_corpus.py [--variant ...]`
"""

import argparse
from pathlib import Path

import yaml

_HERE = Path(__file__).parent
_CHAIN = _HERE / "corpus"

# (店名, 行政区前缀, 开门, 关门, 停止办理分钟, 押金, 卡号前缀, 折扣,
#  券码前缀, 逾期单价, 暂停阈值, 投影仪型号, 投诉响应小时, 仓储月费, 团体上限)
_STORES: tuple[
    tuple[str, str, str, str, int, int, str, int, str, float, int, str, int, int, int], ...
] = (
    (
        "徐汇店",
        "上海",
        "09:00",
        "21:30",
        30,
        30,
        "LY-SH-XH",
        9,
        "QS-SH-01",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "浦东店",
        "上海",
        "09:30",
        "22:00",
        20,
        40,
        "LY-SH-PD",
        88,
        "QS-SH-02",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "静安店",
        "上海",
        "10:00",
        "22:00",
        45,
        20,
        "LY-SH-JA",
        85,
        "QS-SH-03",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "天河店",
        "广州",
        "09:00",
        "22:00",
        30,
        30,
        "LY-GZ-TH",
        88,
        "QS-GZ-01",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "越秀店",
        "广州",
        "09:30",
        "21:30",
        20,
        40,
        "LY-GZ-YX",
        9,
        "QS-GZ-02",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "南山店",
        "深圳",
        "10:00",
        "22:00",
        45,
        20,
        "LY-SZ-NS",
        85,
        "QS-SZ-01",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "福田店",
        "深圳",
        "09:00",
        "21:30",
        30,
        30,
        "LY-SZ-FT",
        88,
        "QS-SZ-02",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "朝阳店",
        "北京",
        "09:30",
        "22:00",
        20,
        40,
        "LY-BJ-CY",
        9,
        "QS-BJ-01",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "海淀店",
        "北京",
        "10:00",
        "22:00",
        45,
        20,
        "LY-BJ-HD",
        85,
        "QS-BJ-02",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "西城店",
        "北京",
        "09:00",
        "21:30",
        30,
        30,
        "LY-BJ-XC",
        88,
        "QS-BJ-03",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "锦江店",
        "成都",
        "09:30",
        "22:00",
        20,
        40,
        "LY-CD-JJ",
        9,
        "QS-CD-01",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "高新店",
        "成都",
        "10:00",
        "21:30",
        45,
        20,
        "LY-CD-GX",
        85,
        "QS-CD-02",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "江汉店",
        "武汉",
        "09:00",
        "22:00",
        30,
        30,
        "LY-WH-JH",
        88,
        "QS-WH-01",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "武昌店",
        "武汉",
        "09:30",
        "21:30",
        20,
        40,
        "LY-WH-WC",
        9,
        "QS-WH-02",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "和平店",
        "沈阳",
        "10:00",
        "22:00",
        45,
        20,
        "LY-SY-HP",
        85,
        "QS-SY-01",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "沈河店",
        "沈阳",
        "09:00",
        "21:30",
        30,
        30,
        "LY-SY-SH",
        88,
        "QS-SY-02",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "雁塔店",
        "西安",
        "09:30",
        "22:00",
        20,
        40,
        "LY-XA-YT",
        9,
        "QS-XA-01",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "碑林店",
        "西安",
        "10:00",
        "21:30",
        45,
        20,
        "LY-XA-BL",
        85,
        "QS-XA-02",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "西湖店",
        "杭州",
        "09:00",
        "22:00",
        30,
        30,
        "LY-HZ-XH",
        88,
        "QS-HZ-01",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "滨江店",
        "杭州",
        "09:30",
        "21:30",
        20,
        40,
        "LY-HZ-BJ",
        9,
        "QS-HZ-02",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "思明店",
        "厦门",
        "10:00",
        "22:00",
        45,
        20,
        "LY-XM-SM",
        85,
        "QS-XM-01",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "湖里店",
        "厦门",
        "09:00",
        "21:30",
        30,
        30,
        "LY-XM-HL",
        88,
        "QS-XM-02",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "河西店",
        "天津",
        "09:30",
        "22:00",
        20,
        40,
        "LY-TJ-HX",
        9,
        "QS-TJ-01",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "和平北店",
        "天津",
        "10:00",
        "21:30",
        45,
        20,
        "LY-TJ-HB",
        85,
        "QS-TJ-02",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "渝中店",
        "重庆",
        "09:00",
        "22:00",
        30,
        30,
        "LY-CQ-YZ",
        88,
        "QS-CQ-01",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "江北店",
        "重庆",
        "09:30",
        "21:30",
        20,
        40,
        "LY-CQ-JB",
        9,
        "QS-CQ-02",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "金水店",
        "郑州",
        "10:00",
        "22:00",
        45,
        20,
        "LY-ZZ-JS",
        85,
        "QS-ZZ-01",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "二七店",
        "郑州",
        "09:00",
        "21:30",
        30,
        30,
        "LY-ZZ-EQ",
        88,
        "QS-ZZ-02",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "历下店",
        "济南",
        "09:30",
        "22:00",
        20,
        40,
        "LY-JN-LX",
        9,
        "QS-JN-01",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "市南店",
        "青岛",
        "10:00",
        "21:30",
        45,
        20,
        "LY-QD-SN",
        85,
        "QS-QD-01",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "鼓楼店",
        "南京",
        "09:00",
        "22:00",
        30,
        30,
        "LY-NJ-GL",
        88,
        "QS-NJ-01",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "玄武店",
        "南京",
        "09:30",
        "21:30",
        20,
        40,
        "LY-NJ-XW",
        9,
        "QS-NJ-02",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "姑苏店",
        "苏州",
        "10:00",
        "22:00",
        45,
        20,
        "LY-SZ-GS",
        85,
        "QS-SU-01",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
    (
        "工业园区店",
        "苏州",
        "09:00",
        "21:30",
        30,
        30,
        "LY-SU-YQ",
        88,
        "QS-SU-02",
        0.5,
        20,
        "云杉 X5",
        2,
        8,
        10,
    ),
    (
        "滨湖店",
        "无锡",
        "09:30",
        "22:00",
        20,
        40,
        "LY-WX-BH",
        9,
        "QS-WX-01",
        0.4,
        15,
        "云杉 X6",
        1,
        6,
        8,
    ),
    (
        "梁溪店",
        "无锡",
        "10:00",
        "21:30",
        45,
        20,
        "LY-WX-LX",
        85,
        "QS-WX-02",
        0.6,
        25,
        "云杉 X7",
        3,
        10,
        12,
    ),
)

# 需要随规模语料一并提供的链路级文档(原样拷贝,保证跨文档题有依据)
_CHAIN_DOCS = (
    "会员等级政策.md",
    "会员权益说明.md",
    "租赁规则.md",
    "积分规则.md",
    "团体租借规范.md",
    "影片分类规范.md",
    "逾期处理流程.md",
    "门店服务规则.md",
    "会员卡挂失与补办.md",
)


def _store_doc(
    row: tuple[str, str, str, str, int, int, str, int, str, float, int, str, int, int, int],
    *,
    strip_name: bool = False,
) -> str:
    """门店手册正文;strip_name=True 时以「本店」替换店名(店名仅存于文档名)。"""
    region = row[1]
    name, opens, closes, stop, deposit = row[0], row[2], row[3], row[4], row[5]
    card, discount, coupon, overdue, suspend, model = (
        row[6],
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
    )
    complaint = row[12]
    subject = "本店" if strip_name else f"{region}{name}"
    name = "本店" if strip_name else name
    return (
        f"# 营业与押金\n\n{subject}营业时间为每日 {opens} 至 {closes},"
        f"{name}闭店前 {stop} 分钟停止办理新租借;{name}非金卡会员租借每部影片押金 {deposit} 元,"
        "归还验收无误后当场退还。\n\n"
        f"# 会员与折扣\n\n{name}联名卡卡号前缀为 {card};持卡租借租金享 {discount} 折,"
        "折扣仅作用于租金,不含逾期费。\n\n"
        f"# 券码与活动\n\n{name}促销券码以 {coupon} 开头,单笔限用一张;"
        "券码不与团体租借九五折叠加。\n\n"
        f"# 租赁与逾期\n\n{name}逾期归还按每日每部 {overdue} 元计收逾期费;"
        f"{name}逾期费累计超过 {suspend} 元时暂停借阅资格。\n\n"
        f"# 设备租借\n\n{name}对外租赁的便携投影仪型号为 {model},押金 500 元,"
        "租期最长 7 天,可按日续租。\n\n"
        f"# 投诉处理\n\n{name}顾客投诉由当班店长在 {complaint} 小时内首次响应;"
        "结案后 7 天内须完成电话回访。\n"
    )


def _cents(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _questions() -> list[dict[str, object]]:
    questions: list[dict[str, object]] = []
    doc_of = {row[0]: f"门店运营手册·{row[0]}.md" for row in _STORES}
    # 单跳:门店特有值(问句避开原文逐字措辞)
    for idx, row in enumerate(_STORES[:18], start=1):
        name, closes, stop, deposit = row[0], row[3], row[4], row[5]
        card = row[6]
        doc = doc_of[name]
        questions.append(
            {
                "id": f"scale_s_{idx:02d}",
                "question": f"{name}闭店前多久就不办新租借了?",
                "category": "single_hop",
                "gold": [{"doc": doc, "snippets": [f"闭店前 {stop} 分钟停止办理新租借"]}],
            }
        )
        questions.append(
            {
                "id": f"scale_s_{idx:02d}b",
                "question": f"{name}租一部影片要交多少保证金?",
                "category": "single_hop",
                "gold": [{"doc": doc, "snippets": [f"押金 {deposit} 元"]}],
            }
        )
    for idx, row in enumerate(_STORES[:12], start=20):
        name, card = row[0], row[6]
        doc = doc_of[name]
        questions.append(
            {
                "id": f"scale_s_{idx:02d}",
                "question": f"以 {card} 开头的会员卡是哪家门店发的?",
                "category": "single_hop",
                "gold": [{"doc": doc, "snippets": [f"卡号前缀为 {card}"]}],
            }
        )
    for idx, row in enumerate(_STORES[:10], start=32):
        name, overdue = row[0], row[9]
        doc = doc_of[name]
        questions.append(
            {
                "id": f"scale_s_{idx:02d}",
                "question": f"{name}的逾期费一天一部收多少钱?",
                "category": "single_hop",
                "gold": [{"doc": doc, "snippets": [f"每日每部 {_cents(overdue)} 元计收逾期费"]}],
            }
        )
    # 同文档多跳:营业时间 + 押金/投诉
    for idx, row in enumerate(_STORES[:8], start=1):
        name, closes, stop, deposit = row[0], row[3], row[4], row[5]
        doc = doc_of[name]
        questions.append(
            {
                "id": f"scale_m_{idx:02d}",
                "question": f"{name}几点打烊?最晚提前多久停止办理租借,押金多少?",
                "category": "multi_hop_doc",
                "gold": [
                    {
                        "doc": doc,
                        "snippets": [
                            f"{_store_open(row)} 至 {closes}",
                            f"闭店前 {stop} 分钟停止办理新租借",
                            f"押金 {deposit} 元",
                        ],
                    }
                ],
            }
        )
    # 跨文档多跳:门店条款 + 链路文档
    for idx, row in enumerate(_STORES[:8], start=1):
        name, suspend = row[0], row[10]
        doc = doc_of[name]
        questions.append(
            {
                "id": f"scale_c_{idx:02d}",
                "question": f"{name}欠费超过多少会停借?暂停后还能续借吗?",
                "category": "multi_hop_cross",
                "gold": [
                    {"doc": doc, "snippets": [f"逾期费累计超过 {suspend} 元时暂停借阅资格"]},
                    {"doc": "逾期处理流程.md", "snippets": ["暂停期间续借申请一律驳回"]},
                ],
            }
        )
    return questions


def _store_open(
    row: tuple[str, str, str, str, int, int, str, int, str, float, int, str, int, int, int],
) -> str:
    return row[2]


class _IndentedDumper(yaml.SafeDumper):
    """块序列相对父键再缩进一级(与仓库 yaml 风格一致)。"""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def main() -> None:
    """按变体写出语料目录、题集与负例集;两形态共用同一批题面与 gold。"""
    parser = argparse.ArgumentParser(description="生成实验室规模语料与题集")
    parser.add_argument(
        "--variant",
        choices=("context", "stripped"),
        default="context",
        help="context=正文含店名;stripped=正文剥离店名(块索引上下文判别语料)",
    )
    args = parser.parse_args()
    stripped = args.variant == "stripped"
    out = _HERE / ("corpus_scale_v1" if stripped else "corpus_scale")
    dataset_name = "retrieval_scale_v1.yaml" if stripped else "retrieval_scale.yaml"
    out.mkdir(exist_ok=True)
    for row in _STORES:
        (out / f"门店运营手册·{row[0]}.md").write_text(
            _store_doc(row, strip_name=stripped), "utf-8"
        )
    for name in _CHAIN_DOCS:
        (out / name).write_text((_CHAIN / name).read_text("utf-8"), "utf-8")
    questions = _questions()
    dataset = {
        "dataset": {
            "tenant": "default",
            "datasource_key": "default",
            "corpus_dir": out.name,
            "top_k": 5,
            "min_questions": 60,
            "min_multihop": 12,
        },
        "questions": questions,
    }
    header = (
        "# 判别语料题集:正文剥离店名(店名仅存于文档名;块索引上下文判别器)"
        if stripped
        else "# 实验室规模语料题集"
    )
    (_HERE / dataset_name).write_text(
        f"{header}(生成器产出,勿手改;重新生成:generate_scale_corpus.py --variant "
        f"{args.variant})\n"
        + yaml.dump(dataset, Dumper=_IndentedDumper, allow_unicode=True, sort_keys=False),
        "utf-8",
    )
    docs = len(_STORES) + len(_CHAIN_DOCS)
    print(f"variant={args.variant} docs={docs} questions={len(questions)}")


if __name__ == "__main__":
    main()
