# Python TUI 绘制 K 线分析工具 —— 技术调研报告

> 调研时间：2025-06-29
> 目标：选型一个 Python TUI 框架，支持**分栏布局**（类似 OpenCode 风格）+ **绘制 K 线/曲线** + **表格展示数据**

---

## 一、框架选型结论

### 推荐：Textual

Textual 是最优选择，理由：

| 需求 | Textual 能力 |
|------|-------------|
| 分栏布局 | 原生 `Horizontal` / `Vertical` 容器 + CSS 布局（`fr` 单位），响应式 |
| 绘制曲线 | `textual-plotext` 插件，一行 `.plot()` 出图 |
| 绘制 K 线 | plotext 原生 `candlestick()` 函数，直接传入 OHLC 数据 |
| 表格 | 内置 `DataTable` 控件，支持排序/行列导航/选中事件 |
| 缩放 | CSS 布局自动适应终端 resize，`on_resize` 事件可做细粒度控制 |

### 排除的其他选项

| 库 | 原因 |
|----|------|
| **Urwid** | 太底层，分栏和曲线都得手撸，等于造轮子 |
| **Rich** | 纯渲染库，无交互能力（Textual 的内核） |
| **curses** | 原始终端库，全手撸，不推荐 |

---

## 二、曲线 & K 线绘制方案

### 方案 A（推荐）：textual-plotext

直接插件化集成 plotext 到 Textual：

```python
from textual_plotext import PlotextPlot

class KlinePanel(PlotextPlot):
    def on_mount(self):
        df = pd.read_parquet("data.parquet")
        self.plt.candlestick(
            df["date"], df["open"], df["high"],
            df["low"], df["close"]
        )
        self.plt.title("日 K 线")
```

**支持图形：** 折线、散点、柱状、面积、直方图、K 线（candlestick）、箱线图、混淆矩阵

**Pandas 兼容性：** pandas Series 可直接传入 `.plot()` / `.candlestick()`，无需转列表

**数据量注意：** 终端渲染有分辨率限制，建议只展示最近 200-500 根 K 线，过多会挤压

### 方案 B：ChartingLib

底层用 Plotly 渲染到终端，也原生支持 K 线，但依赖 Plotly 体积较大。

### 方案 C：Textual 内置 Canvas

自由度最高，但需要自行计算坐标、绘制像素点。除非对渲染有极端要求，否则不推荐。

---

## 三、表格能力（DataTable）

Textual 内置 `DataTable` 控件能力一览：

| 功能 | 状态 |
|------|------|
| 行列导航 | `cursor_type = "cell"/"row"/"column"` |
| 排序 | `table.sort("col", reverse=True)`，支持自定义 key 函数 |
| 键盘操作 | 方向键、PageUp/Down、Home/End |
| 鼠标操作 | 悬停高亮、点击选中 |
| 事件回调 | `CellSelected`、`RowSelected`、`ColumnSelected` |
| 单元格格式化 | 支持 Rich Text 对象（颜色/粗体/小数位数控制） |
| 列宽自适应 | 自定义 `on_resize` 按比例分配，或 CSS `fr` 单位 |

**局限性：** 无内置冻结列、合并单元格、表头分组，如需这些需自行封装

---

## 四、布局与缩放

Textual 使用 **CSS 样式表** 描述布局：

```css
Screen {
    layout: horizontal;
}

#left-panel {
    width: 30%;
    height: 100%;
}

#right-panel {
    width: 1fr;
    height: 100%;
}
```

- `fr`（fraction）单位自动分配剩余空间，终端 resize 时自动重排
- `min-width` / `max-height` 约束生效
- 可挂钩 `on_resize` 事件做自定义的缩放逻辑（如 K 线显示范围跟随窗口宽度）

---

## 五、建议的技术栈

```
Textual             ← TUI 框架（布局 + 交互 + 生命周期）
textual-plotext     ← 终端绘图（K 线 + 折线）
Pandas              ← 数据处理
yfinance / akshare  ← 数据源（可选）
```

### 最小原型结构

```
├── app.py           # Textual App 入口
├── widgets/
│   ├── kline.py     # K 线图面板（PlotextPlot 子类）
│   ├── table.py     # 数据表格面板（DataTable 子类）
│   └── sidebar.py   # 侧边栏（品种列表/指标选择）
├── data/
│   └── loader.py    # 数据加载（Pandas）
└── app.tcss         # 布局样式
```

### 安装

```bash
pip install textual textual-plotext pandas
```

---

## 六、参考链接

- [Textual 官方文档](https://textual.textualize.io/)
- [Textual DataTable 控件](https://textual.textualize.io/widgets/data_table/)
- [textual-plotext 发布公告](https://textual.textualize.io/blog/2023/10/04/announcing-textual-plotext/)
- [textual-plotext GitHub](https://github.com/textualize/textual-plotext)
- [plotext GitHub（底层绘图库）](https://github.com/piccolomo/plotext)
- [ChartingLib（备选 K 线方案）](https://pypi.org/project/chartinglib/)
