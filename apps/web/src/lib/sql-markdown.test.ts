import { describe, expect, it } from "vitest";

import { fencedSql } from "@/lib/sql-markdown";

describe("fencedSql", () => {
  it("普通 SQL 使用三反引号围栏", () => {
    expect(fencedSql("SELECT 1")).toBe("```sql\nSELECT 1\n```");
  });

  it("内容含反引号时扩大围栏,避免提前闭合", () => {
    const text = fencedSql("SELECT '```' AS marker");
    expect(text.startsWith("````sql\n")).toBe(true);
    expect(text.endsWith("\n````")).toBe(true);
  });
});
