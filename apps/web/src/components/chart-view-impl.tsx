import { useMemo, useRef } from "react";
import { ChartColumn, ChartLine, ChartPie, ChartScatter, ImageDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { EChart } from "@/lib/echarts";
import { cssVar } from "@/lib/utils";
import type { ChartDatum, ChartSpec, ChartTune, ChartType } from "@/lib/chart-shared";
import { uniqueTypes } from "@/lib/chart-shared";
import { buildChartOption } from "@/lib/chart-option";

export interface ChartViewProps {
  /** 有序图表候选:每个候选自带完整绑定,活动绑定=同类型首个候选 */
  candidates: ChartSpec[];
  data: ChartDatum;
  tune?: ChartTune | undefined;
  /** 呈现层列标签(缺失/未命中回退物理列名)——多度量系列名用它,禁裸物理名直达用户 */
  labels?: string[] | undefined;
  /** 当前图表类型(状态提升至 chart-view 壳:主题重挂不丢用户手动选择) */
  activeType: ChartType;
  onActiveTypeChange: (t: ChartType) => void;
}

const TYPE_META: Record<ChartType, { label: string; Icon: typeof ChartColumn }> = {
  bar: { label: "柱状图", Icon: ChartColumn },
  line: { label: "折线图", Icon: ChartLine },
  pie: { label: "饼图", Icon: ChartPie },
  scatter: { label: "散点图", Icon: ChartScatter },
};

/* 色板从面向数据的 token 派生;前景文字色不入系列(浅色底上近不可见) */
function chartPalette(): string[] {
  return [
    cssVar("--primary"),
    cssVar("--ring"),
    cssVar("--accent-foreground"),
    cssVar("--muted-foreground"),
  ];
}

/* 下载当前图表为 PNG：背景取卡片底色 token，避免透明底。 */
function downloadChartPng(chart: EChart | null): void {
  const inst = chart?.getEchartsInstance();
  if (!inst) return;
  // 背景取卡片底色 token 防透明底;pixelRatio 2 覆盖汇报贴图清晰度
  const url = inst.getDataURL({
    type: "png",
    pixelRatio: 2,
    backgroundColor: cssVar("--card"),
  });
  const d = new Date();
  const pad = (n: number): string => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  // Safari 形态:锚点须入 DOM 再 click;dataURL 非 blob 无需 revoke
  const a = document.createElement("a");
  a.href = url;
  a.download = `图表-${stamp}.png`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* 图表类型切换器；只有一种候选类型时整组隐藏。 */
function TypeSwitcher({
  types,
  activeType,
  onChange,
}: {
  types: ChartType[];
  activeType: ChartType;
  onChange: (t: ChartType) => void;
}) {
  if (types.length <= 1) return null;
  return (
    <ToggleGroup
      type="single"
      value={activeType}
      onValueChange={(v) => {
        if (v) onChange(v as ChartType); // 单选恒保留一个激活(取消选中不回空)
      }}
      variant="outline"
      size="sm"
      aria-label="图表类型"
    >
      {types.map((t) => {
        const { label, Icon } = TYPE_META[t];
        return (
          <ToggleGroupItem key={t} value={t} aria-label={label} title={label}>
            <Icon aria-hidden />
          </ToggleGroupItem>
        );
      })}
    </ToggleGroup>
  );
}

export default function ChartViewImpl({
  candidates,
  data,
  tune,
  labels,
  activeType,
  onActiveTypeChange,
}: ChartViewProps) {
  const palette = useMemo(chartPalette, []);
  const axisColor = useMemo(() => cssVar("--muted-foreground"), []);
  const lineColor = useMemo(() => cssVar("--border"), []);
  const chartRef = useRef<EChart>(null);
  const toggleTypes = useMemo(() => uniqueTypes(candidates), [candidates]);
  // tune 基元展开入依赖:标题/轴名一改即重算重绘(空串与未提供对 option 等价)
  const tuneTitle = tune?.title;
  const tuneXName = tune?.xName;
  const tuneYName = tune?.yName;

  const option = useMemo(
    () =>
      buildChartOption({
        candidates,
        data,
        labels,
        tune: { title: tuneTitle ?? "", xName: tuneXName ?? "", yName: tuneYName ?? "" },
        activeType,
        palette,
        axisColor,
        lineColor,
      }),
    [
      candidates,
      activeType,
      data,
      labels,
      palette,
      axisColor,
      lineColor,
      tuneTitle,
      tuneXName,
      tuneYName,
    ],
  );

  function downloadPng(): void {
    downloadChartPng(chartRef.current);
  }

  return (
    <div>
      <div
        className={`flex items-center px-2 pt-1 ${
          toggleTypes.length > 1 ? "justify-between" : "justify-end"
        }`}
      >
        <TypeSwitcher types={toggleTypes} activeType={activeType} onChange={onActiveTypeChange} />
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={downloadPng}
          aria-label="下载图表图片"
          title="下载图片"
        >
          <ImageDown aria-hidden />
        </Button>
      </div>
      <EChart
        ref={chartRef}
        option={option}
        notMerge
        lazyUpdate
        style={{ height: 260, width: "100%" }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}
