"""对拍基线回放:冻结期望集(消费方内置实现导出)与引擎判定逐例比对。

规则移植漂移在此即红;fixture 缺失/版本不符直接失败(文件随仓入库)。
"""

from pathlib import Path

from liannu.fixtures import Mismatch, load_fixtures, replay

FIXTURE = Path(__file__).parent / "fixtures" / "kongming_baseline.json"


def test_baseline_replay_zero_mismatch() -> None:
    data = load_fixtures(FIXTURE)
    mismatches: list[Mismatch] = replay(data)
    assert not mismatches, f"对拍不一致 {len(mismatches)} 处:\n" + "\n".join(
        repr(m) for m in mismatches
    )


def test_baseline_has_expected_sections() -> None:
    data = load_fixtures(FIXTURE)
    assert data["schema_version"] == 1
    assert len(data.get("cases", [])) >= 40  # sqlfw/datascope 测试 + evals gold 汇入
    assert len(data.get("depth_cases", [])) >= 2  # 深嵌套受控行为钉样
