from ibm_watsonx_orchestrate.agent_builder.tools.python_tool import tool


@tool
def test_chart() -> bytes:
    """
    テスト用のPlotlyグラフをHTMLで生成して返す

    Returns:
        PlotlyグラフのHTML（bytes）。WxOがS3に自動アップロードする。
    """
    html = """<html>
<head><meta charset="utf-8"/></head>
<body>
<div id="g"></div>
<script src="https://cdn.plot.ly/plotly-3.4.0.min.js"></script>
<script>
Plotly.newPlot("g", [{
    x: [2020, 2021, 2022, 2023, 2024],
    y: [10, 15, 20, 25, 30],
    type: "scatter",
    name: "テストデータ"
}], {
    title: "S3アップロードテスト",
    width: 800,
    height: 400
});
</script>
</body>
</html>"""
    return html.encode("utf-8")
