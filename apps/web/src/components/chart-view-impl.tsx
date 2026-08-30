import { useMemo, useRef } from "react";
import { ChartColumn, ChartLine, ChartPie, ChartScatter, ImageDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { EChart, themedTooltip } from "@/lib/echarts";
import type { ChartDatum, ChartSpec, ChartTune, ChartType } from "@/components/chart-shared";
import { uniqueTypes } from "@/components/chart-shared";

export interface ChartViewProps {
  /** 有序图表候选(049):每个候选自带完整绑定,活动绑定=同类型首个候选 */
  candidates: ChartSpec[];
  data: ChartDatum;
  tune?: ChartTune;
  /** 041 呈现层列标签(缺失/未命中回退物理列名)——多度量系列名用它,禁裸物理名直达用户 */
  labels?: string[];
  /** 当前图表类型(状态提升至 chart-view 壳:主题重挂不丢用户手动选择,040) */
  activeType: ChartType;
  onActiveTypeChange: (t: ChartType) => void;
}

const TYPE_META: Record<ChartType, { label: string; Icon: typeof ChartColumn }> = {
  bar: { label: "柱状图", Icon: ChartColumn },
  line: { label: "折线图", Icon: ChartLine },
  pie: { label: "饼图", Icon: ChartPie },
  scatter: { label: "散点图", Icon: ChartScatter },
};

/* 从 CSS token 派生图表色板:朱砂打底、鎏金点睛、青灰过渡,惜色如金;
   只用面向数据的 token,前景文字色不入系列(浅色底上近不可见) */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartPalette(): string[] {
  return [cssVar("--primary"), cssVar("--ring"), cssVar("--accent-foreground"), cssVar("--muted-foreground")];
}

/* 空值显式排除:JS Number(null)===0 / Number("")===0 会把空单元格误判为数值 0;
   空白串同理(Number("  ")===0),一并拦下 */
function isNumericCell(v: unknown): boolean {
  if (v === null || v === undefined || typeof v === "boolean") return false;
  if (typeof v === "string" && v.trim() === "") return false;
  return Number.isFinite(Number(v));
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/* temporal 轴标签本地化(011 挂账⑤/013):PG 日期序列化为 ISO 原文
   (2005-07-05T00:00:00)不可读。全列匹配 ISO 日期形态才启用(任一不匹配
   即整列原样,宁不错格式化——编号串不可能匹配完整形态);启用时呈现日期
   部分,时间非全零附加 HH:mm。不引日期库(截断即可),轴与 tooltip 同源。 */
const ISO_DATE_RE = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/;

function formatTemporalLabels(labels: string[]): string[] {
  if (labels.length === 0 || !labels.every((l) => ISO_DATE_RE.test(l))) return labels;
  return labels.map((l) => {
    const m = ISO_DATE_RE.exec(l);
    if (!m) return l;
    return m[2] === "00" && m[3] === "00" ? m[1] : `${m[1]} ${m[2]}:${m[3]}`;
  });
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

  const option = useMemo(() => {
    // 活动绑定:同类型取首个候选(切换类型即切换其完整 x/y/系列映射)
    const binding = candidates.find((c) => c.type === activeType) ?? candidates[0];
    const xi = binding.x ? data.columns.indexOf(binding.x) : 0;
    const yNames = binding.y.length > 0 ? binding.y : [data.columns[1] ?? ""];
    const yIdxs = yNames
      .map((n) => data.columns.indexOf(n))
      .filter((i) => i >= 0);
    const useYIdxs = yIdxs.length > 0 ? yIdxs : [1];
    const xLabels = formatTemporalLabels(data.rows.map((r) => String(r[xi] ?? "")));
    const axisCommon = {
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: lineColor } },
    };
    // 浮层 token 色:默认白底 tooltip 在暗色页面里刺眼(040 主题联动);
    // 构造单源 lib/echarts.themedTooltip(与两统计页同源,禁第二份)
    const tooltipCommon = themedTooltip();
    // 026 调参:标题非空才占位(顶部留白随有无标题切换,防空标题压图)
    const titleOpt = tune?.title
      ? {
          title: {
            text: tune.title,
            left: "center",
            top: 4,
            textStyle: { color: axisColor, fontSize: 13, fontWeight: 500 },
          },
        }
      : {};
    const xNameOpt = tune?.xName ? { name: tune.xName, nameTextStyle: { color: axisColor } } : {};
    const yNameOpt = tune?.yName ? { name: tune.yName, nameTextStyle: { color: axisColor } } : {};

    if (activeType === "pie") {
      // pie 恒单 y(候选生成保证;异常多 y 时取首个)
      const values = data.rows.map((r) => {
        const v = r[useYIdxs[0]];
        return typeof v === "number" ? v : Number(v);
      });
      return {
        ...titleOpt,
        tooltip: { trigger: "item", ...tooltipCommon },
        legend: { bottom: 0, left: "center", textStyle: { color: axisColor } },
        series: [
          {
            type: "pie",
            radius: ["38%", "66%"],
            center: ["50%", "44%"],
            data: xLabels.map((name, i) => ({ name, value: values[i] })),
            color: palette,
            label: { show: false },
            labelLine: { show: false },
          },
        ],
      };
    }

    const grid = {
      left: 12,
      right: 16,
      top: tune?.title ? 36 : 24,
      bottom: 8,
      containLabel: true,
    };

    if (activeType === "scatter") {
      const yi = useYIdxs[0];
      const xNumeric =
        data.rows.length > 0 && data.rows.every((r) => isNumericCell(r[xi]));
      if (xNumeric) {
        return {
          ...titleOpt,
          grid,
          tooltip: {
            trigger: "item",
            ...tooltipCommon,
            // 列名+数值双行呈现(011 挂账⑨/013),与 bar/line 信息量对齐;
            // y=null 空值点 echarts 不渲染,无 tooltip 触发,?? "—" 纯防御
            formatter: (p: unknown) => {
              const v = (p as { value: [number, number | null] }).value;
              return `${binding.x ?? "x"}: ${v[0]}<br/>${binding.y[0] ?? "y"}: ${v[1] ?? "—"}`;
            },
          },
          // scale:true:轴不含零起点,窄区间远离 0 的数据不挤角(散点看分布)
          xAxis: { type: "value", scale: true, ...xNameOpt, ...axisCommon },
          yAxis: { type: "value", scale: true, ...yNameOpt, ...axisCommon },
          series: [
            {
              type: "scatter",
              // y 空值/非数值映 null(echarts 跳过该点),防 Number(null)===0 落假点
              data: data.rows.map((r) => [
                Number(r[xi]),
                isNumericCell(r[yi]) ? Number(r[yi]) : null,
              ]),
              itemStyle: { color: palette[0] },
            },
          ],
        };
      }
      // 退化:类目轴点图(一行非数值即整体退化,宁退化不丢点;
      // y 沿用单系列的 Number() 既有语义,与 bar/line 同构)
      const values = data.rows.map((r) => {
        const v = r[yi];
        return typeof v === "number" ? v : Number(v);
      });
      return {
        ...titleOpt,
        grid,
        tooltip: { trigger: "axis", ...tooltipCommon },
        xAxis: { type: "category", data: xLabels, ...xNameOpt, ...axisCommon },
        yAxis: { type: "value", ...yNameOpt, ...axisCommon },
        series: [
          { type: "scatter", data: values, itemStyle: { color: palette[0] } },
        ],
      };
    }

    // bar/line:多系列=系列维绑定(分组柱/时序多系列)或多度量(y 列表);
    // 分组/多度量空值映 null(稀疏组合不落零柱),单系列沿用 Number() 既有语义
    const si = binding.series ? data.columns.indexOf(binding.series) : -1;
    // 分组系列:类目轴=x 唯一值全集(首次出现序),各系列按「系列值×类目」
    // 对号取数、缺组合补 null——若逐行过滤,系列数据下标≠类目下标,会产生
    // 柱体挤半轴与值归属错类目(049 实测缺陷,用户肉眼抓出)
    const hasSeries = si >= 0;
    const rawCats = [...new Set(data.rows.map((r) => String(r[xi] ?? "")))];
    const axisCats = formatTemporalLabels(rawCats);
    // 类目轴标签分级(050 实测反馈):抽样显示是 echarts 防重叠默认,但柱状图
    // 不给全类目名=读者只能靠 tooltip 认柱——少量全显横排、中量旋转全显、
    // 大量(>24)才交回 echarts 自动抽稀
    const catCount = hasSeries ? axisCats.length : xLabels.length;
    const catLabelOpt =
      catCount <= 12
        ? { interval: 0 }
        : catCount <= 24
          ? { interval: 0, rotate: 35 }
          : {};
    let seriesList: { name?: string; data: (number | null)[] }[];
    if (hasSeries) {
      const lookup = new Map<string, number | null>();
      for (const r of data.rows) {
        const v = r[useYIdxs[0]];
        lookup.set(
          `${String(r[si] ?? "")}||${String(r[xi] ?? "")}`,
          isNumericCell(v) ? Number(v) : null,
        );
      }
      const seriesVals = [...new Set(data.rows.map((r) => String(r[si] ?? "")))];
      seriesList = seriesVals.map((sv) => ({
        name: sv,
        data: rawCats.map((cat) => lookup.get(`${sv}||${cat}`) ?? null),
      }));
    } else if (useYIdxs.length > 1) {
      seriesList = useYIdxs.map((yi) => ({
        // 041 双轨:系列名经列标签(缺失回退物理列名),不裸投物理名
        name: labels?.[yi] || data.columns[yi],
        data: data.rows.map((r) => {
          const v = r[yi];
          return isNumericCell(v) ? Number(v) : null;
        }),
      }));
    } else {
      seriesList = [
        {
          data: data.rows.map((r) => {
            const v = r[useYIdxs[0]];
            return typeof v === "number" ? v : Number(v);
          }),
        },
      ];
    }
    const multi = seriesList.length > 1;
    const legendOpt = multi
      ? { legend: { bottom: 0, left: "center", textStyle: { color: axisColor } } }
      : {};
    // 底部图例占位:压 8px 底距为 28px,防图例与轴标签重叠
    const gridFinal = multi ? { ...grid, bottom: 28 } : grid;
    // 全整数量测(计数类)→ minInterval 1:小值域不产生 0.5/1.5 半数碎刻度
    const allInts = seriesList.every((s) =>
      s.data.every((v) => v === null || Number.isInteger(v)),
    );
    return {
      ...titleOpt,
      ...legendOpt,
      grid: gridFinal,
      tooltip: { trigger: "axis", ...tooltipCommon },
      xAxis: {
        type: "category",
        data: hasSeries ? axisCats : xLabels,
        axisLabel: { ...catLabelOpt, ...axisCommon.axisLabel },
        ...xNameOpt,
        axisLine: axisCommon.axisLine,
      },
      yAxis: {
        type: "value",
        ...(allInts ? { minInterval: 1 } : {}),
        ...yNameOpt,
        ...axisCommon,
      },
      series: seriesList.map((s, k) =>
        activeType === "line"
          ? {
              type: "line",
              name: s.name,
              data: s.data,
              smooth: true,
              itemStyle: { color: palette[k % palette.length] },
              lineStyle: { color: palette[k % palette.length], width: 2 },
            }
          : {
              type: "bar",
              name: s.name,
              data: s.data,
              itemStyle: { color: palette[k % palette.length], borderRadius: [3, 3, 0, 0] },
              barMaxWidth: 40,
            },
      ),
    };
    // tune 基元展开入依赖(标题/轴名一改即重算重绘,026 调参语义)
  }, [candidates, activeType, data, labels, palette, axisColor, lineColor, tune?.title, tune?.xName, tune?.yName]);

  function downloadPng(): void {
    const inst = chartRef.current?.getEchartsInstance();
    if (!inst) return;
    // 背景取卡片底色 token 防透明底;pixelRatio 2 覆盖汇报贴图清晰度
    const url = inst.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: cssVar("--card"),
    });
    const d = new Date();
    const stamp = `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
    // Safari 形态:锚点须入 DOM 再 click(010 教训);dataURL 非 blob 无需 revoke
    const a = document.createElement("a");
    a.href = url;
    a.download = `图表-${stamp}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  return (
    <div>
      <div
        className={`flex items-center px-2 pt-1 ${
          toggleTypes.length > 1 ? "justify-between" : "justify-end"
        }`}
      >
        {/* 单候选隐藏切换器(spec 049):只渲染候选类型,顺序=候选序 */}
        {toggleTypes.length > 1 && (
          <ToggleGroup
            type="single"
            value={activeType}
            onValueChange={(v) => {
              if (v) onActiveTypeChange(v as ChartType); // 单选恒保留一个激活(取消选中不回空)
            }}
            variant="outline"
            size="sm"
            aria-label="图表类型"
          >
            {toggleTypes.map((t) => {
              const { label, Icon } = TYPE_META[t];
              return (
                <ToggleGroupItem key={t} value={t} aria-label={label} title={label}>
                  <Icon aria-hidden />
                </ToggleGroupItem>
              );
            })}
          </ToggleGroup>
        )}
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
