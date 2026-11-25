from nego.nodash import no_dash


def test_no_dash():
    assert no_dash("없습니다 — 확인") == "없습니다. 확인"
    assert no_dash("자격 — 미달") == "자격: 미달"
    assert no_dash("<td>—</td>") == "<td>-</td>"
    assert no_dash("2025—2026\n- 항목") == "2025—2026\n- 항목"
