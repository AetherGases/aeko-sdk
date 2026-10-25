"""Generate a GHG Protocol-style inventory workbook for Aeko analysis tests."""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.chart import PieChart, Reference
from openpyxl.chart.label import DataLabelList


OUT = Path(__file__).with_name("inventario-ghg-celulose-2025.xlsx")

GREEN = "1B7A5A"
GREEN_DARK = "145C44"
GREEN_SOFT = "E6F5EE"
GOLD = "C9A227"
GOLD_SOFT = "FBF4DE"
INK = "1F2933"
MUTED = "5B6570"
WHITE = "FFFFFF"
LINE = "C5D4CC"
BLUE_SOFT = "E8F1FA"

thin = Border(
    left=Side(style="thin", color=LINE),
    right=Side(style="thin", color=LINE),
    top=Side(style="thin", color=LINE),
    bottom=Side(style="thin", color=LINE),
)
fill_green = PatternFill("solid", fgColor=GREEN)
fill_green_dark = PatternFill("solid", fgColor=GREEN_DARK)
fill_soft = PatternFill("solid", fgColor=GREEN_SOFT)
fill_gold = PatternFill("solid", fgColor=GOLD_SOFT)
fill_white = PatternFill("solid", fgColor=WHITE)
fill_alt = PatternFill("solid", fgColor="F4FAF7")
fill_blue = PatternFill("solid", fgColor=BLUE_SOFT)
font_title = Font(name="Calibri", size=18, bold=True, color=GREEN_DARK)
font_section = Font(name="Calibri", size=13, bold=True, color=GREEN_DARK)
font_head = Font(name="Calibri", size=10, bold=True, color=WHITE)
font_label = Font(name="Calibri", size=10, bold=True, color=INK)
font_body = Font(name="Calibri", size=10, color=INK)
font_muted = Font(name="Calibri", size=10, italic=True, color=MUTED)
font_total = Font(name="Calibri", size=10, bold=True, color=GREEN_DARK)
center = Alignment(horizontal="center", vertical="center", wrap_text=True)
left = Alignment(horizontal="left", vertical="center", wrap_text=True)


def style_header_row(ws, row, start, end, fill=fill_green):
    ws.row_dimensions[row].height = 28
    for col in range(start, end + 1):
        cell = ws.cell(row, col)
        cell.fill = fill
        cell.font = font_head
        cell.alignment = center
        cell.border = thin


def set_widths(ws, widths):
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def kv(ws, row, label, value, fill=fill_soft):
    ws.cell(row, 1, label).font = font_label
    ws.cell(row, 1).fill = fill
    ws.cell(row, 1).border = thin
    ws.cell(row, 1).alignment = left
    cell = ws.cell(row, 2, value)
    cell.font = font_body
    cell.border = thin
    cell.alignment = left
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)


EMISSIONS = [
    # fonte, atividade, unidade, escopo, categoria, classificacao, gas, formula, tco2e, metodologia, pct, biogenico, observacao
    (
        "E-01",
        "Caldeira de vapor — combustão de gás natural",
        "Casa de força",
        "Scope 1",
        "Combustão estacionária",
        None,
        "Dióxido de carbono",
        "CO2",
        600.0,
        "GHG Protocol Brasil",
        92.0,
        "Não",
        "Queimadores originais; sem substituição por gás verde.",
    ),
    (
        "E-02",
        "CH4 residual da digestão anaeróbica de efluentes",
        "ETE / biodigestor",
        "Scope 1",
        "Combustão estacionária",
        None,
        "Metano",
        "CH4",
        450.0,
        "Balanço de massa",
        78.0,
        "Não",
        "Captura de biogás estimada em 70%. Os 30% restantes escapam como CH4.",
    ),
    (
        "E-03",
        "Vazamentos de HFC-134a no circuito de refrigeração",
        "Linha de branqueamento",
        "Scope 1",
        "Combustão estacionária",
        None,
        "Hidrofluorcarbonetos",
        "HFCs",
        85.0,
        "Fator de emissão do fornecedor",
        64.0,
        "Não",
        "Dois vazamentos identificados em 2025. Sem sistema de captura.",
    ),
    (
        "E-04",
        "Frota própria de caminhões a diesel (pátio e mata)",
        "Logística interna",
        "Scope 1",
        "Combustão móvel",
        None,
        "Dióxido de carbono",
        "CO2",
        180.0,
        "GHG Protocol Brasil",
        88.0,
        "Não",
        "18 caminhões; nenhum convertido para biometano.",
    ),
    (
        "E-05",
        "N2O da combustão da frota diesel",
        "Logística interna",
        "Scope 1",
        "Combustão móvel",
        None,
        "Óxido nitroso",
        "N2O",
        12.0,
        "GHG Protocol Brasil",
        88.0,
        "Não",
        "Emissão residual da combustão móvel.",
    ),
    (
        "E-06",
        "Eletricidade comprada da rede (fator médio SIN)",
        "Planta inteira",
        "Scope 2",
        "Energia elétrica adquirida",
        None,
        "Dióxido de carbono",
        "CO2",
        2000.0,
        "Fator de emissão do fornecedor",
        100.0,
        "Não",
        "30% já coberto por I-REC eólico. Os 70% restantes são grid.",
    ),
    (
        "E-07",
        "Insumos químicos e serviços contratados",
        "Compras",
        "Scope 3",
        "Bens e serviços adquiridos",
        "UPSTREAM",
        "Dióxido de carbono",
        "CO2",
        980.0,
        "Fator de emissão do fornecedor",
        41.0,
        "Não",
        "Dados primários parciais dos principais fornecedores de químicos.",
    ),
    (
        "E-08",
        "Capex de 2025 — reformadora da linha 2",
        "Engenharia",
        "Scope 3",
        "Bens de capital",
        "UPSTREAM",
        "Dióxido de carbono",
        "CO2",
        420.0,
        "Estimativa por atividade",
        22.0,
        "Não",
        "Embodied carbon do equipamento importado.",
    ),
    (
        "E-09",
        "Transporte de madeira de terceiros até a fábrica",
        "Suprimentos florestais",
        "Scope 3",
        "Transporte e distribuição upstream",
        "UPSTREAM",
        "Dióxido de carbono",
        "CO2",
        1800.0,
        "Balanço de massa",
        54.0,
        "Não",
        "Maior linha de Escopo 3. Frota de terceiros 100% diesel.",
    ),
    (
        "E-10",
        "CH4 de resíduos orgânicos enviados a aterro",
        "Gestão de resíduos",
        "Scope 3",
        "Resíduos gerados nas operações",
        "UPSTREAM",
        "Metano",
        "CH4",
        210.0,
        "Fator de emissão do fornecedor",
        60.0,
        "Não",
        "Lodo não aproveitado no biodigestor.",
    ),
    (
        "E-11",
        "Viagens a negócios (aéreo + hospedagem)",
        "Administrativo",
        "Scope 3",
        "Viagens a negócios",
        "UPSTREAM",
        "Dióxido de carbono",
        "CO2",
        45.0,
        "GHG Protocol Brasil",
        70.0,
        "Não",
        None,
    ),
    (
        "E-12",
        "Deslocamento casa-trabalho dos turnos",
        "RH",
        "Scope 3",
        "Deslocamento de funcionários",
        "UPSTREAM",
        "Dióxido de carbono",
        "CO2",
        90.0,
        "Estimativa por atividade",
        15.0,
        "Não",
        "Pesquisa amostral com 18% de resposta.",
    ),
    (
        "E-13",
        "Distribuição de celulose até clientes",
        "Logística de saída",
        "Scope 3",
        "Transporte e distribuição downstream",
        "DOWNSTREAM",
        "Dióxido de carbono",
        "CO2",
        900.0,
        "Fator de emissão do fornecedor",
        48.0,
        "Não",
        "Rodoviário 80%, ferroviário 20%.",
    ),
    (
        "E-14",
        "Fim de vida de embalagens e aparas junto ao cliente",
        "Pós-venda",
        "Scope 3",
        "Tratamento de fim de vida",
        "DOWNSTREAM",
        "Dióxido de carbono",
        "CO2",
        160.0,
        "Estimativa por atividade",
        12.0,
        "Não",
        None,
    ),
]

BIOGENIC = [
    (
        "B-01",
        "Caldeira de biomassa — casca e licor negro",
        "Casa de força",
        "Scope 1",
        "Combustão estacionária",
        None,
        "Dióxido de carbono biogênico",
        "CO2(b)",
        3500.0,
        "Balanço de massa",
        96.0,
        "Sim",
        "Floresta plantada com manejo FSC. Reportar fora do total de GEE, conforme GHG Protocol.",
    ),
    (
        "B-02",
        "Biogás capturado queimado na caldeira auxiliar",
        "ETE / biodigestor",
        "Scope 1",
        "Combustão estacionária",
        None,
        "Dióxido de carbono biogênico",
        "CO2(b)",
        190.0,
        "Balanço de massa",
        82.0,
        "Sim",
        "Gás verde já em uso. A captura incompleta gera o CH4 da linha E-02.",
    ),
]

REDUCTIONS = [
    (
        "R-01",
        "Contrato I-REC eólico cobrindo 30% da eletricidade",
        "Energia elétrica adquirida",
        420.0,
        "Redução de Escopo 2 já realizada em 2025.",
    ),
    (
        "R-02",
        "Otimização de rotas da madeira de terceiros",
        "Transporte e distribuição upstream",
        180.0,
        "Consolidação de cargas; frota ainda diesel.",
    ),
    (
        "R-03",
        "Substituição parcial de gás natural por biogás capturado",
        "Combustão estacionária",
        380.0,
        "Gás verde já implementado. Potencial de captura adicional ~30%.",
    ),
]


def build():
    wb = Workbook()

    # ------------------------------------------------------------------ capa
    capa = wb.active
    capa.title = "Instrucoes"
    set_widths(capa, [28, 28, 28, 28, 28, 22])
    capa.merge_cells("A1:F1")
    capa["A1"] = "Inventário de GEE — padrão GHG Protocol (fixture de teste Aeko)"
    capa["A1"].font = font_title
    capa.row_dimensions[1].height = 28
    capa.merge_cells("A2:F2")
    capa["A2"] = (
        "Arquivo sintético no recorte do template GHG usado pelo Aether: "
        "Parte 1 (informações do inventário), Parte 2 (emissões de GEE), "
        "Parte 3 (emissões biogênicas), reduções e resumo. "
        "Pronto para upload no fluxo de análise de inventários."
    )
    capa["A2"].font = font_muted
    capa["A2"].alignment = left
    capa.row_dimensions[2].height = 48

    capa["A4"] = "Como usar"
    capa["A4"].font = font_section
    steps = [
        "1. No widget/API de análise, envie este XLSX com o nome sugerido "
        "“Inventário Celulose Ponta Grossa 2025”.",
        "2. O analisador espera o inventário como tabela (markdown depois da conversão). "
        "As abas Parte2 e Parte3 são o miolo.",
        "3. Os nomes de escopo, categoria e gás batem com o catálogo seed do Aether "
        "(Scope 1/2/3, 12 categorias, 6 gases).",
        "4. Há poluentes (CO2, CH4, N2O, HFCs) e gases verdes já em uso (biogás, I-REC), "
        "para exercitar os dois analistas depois do Análista de inventários.",
        "5. Linhas em branco no fim das tabelas de emissão/redução podem ser preenchidas "
        "se quiser variar o teste.",
    ]
    for i, text in enumerate(steps, start=5):
        capa.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        capa.cell(i, 1, text).font = font_body
        capa.cell(i, 1).alignment = left
        capa.row_dimensions[i].height = 32

    capa["A11"] = "Abas"
    capa["A11"].font = font_section
    sheets_info = [
        ("Instrucoes", "Esta capa."),
        ("Parte1_Informacoes", "Metadados do inventário (descrição, período, consolidação)."),
        ("Parte2_Emissoes", "Emissões antropogênicas em tCO2e — o que entra no total GEE."),
        ("Parte3_Biogenicas", "CO2 biogênico reportado à parte, como no GHG Protocol."),
        ("Reducoes", "Iniciativas já realizadas (is_reduction = true)."),
        ("Resumo", "Totais por escopo, gás e categoria, com gráfico."),
        ("Catalogo", "Ids vigentes de gases, escopos e categorias no seed Aether."),
    ]
    style_header_row(capa, 12, 1, 2)
    capa.cell(12, 1, "Aba")
    capa.cell(12, 2, "Conteúdo")
    capa.merge_cells("B12:F12")
    for i, (name, desc) in enumerate(sheets_info, start=13):
        capa.cell(i, 1, name).font = font_label
        capa.cell(i, 1).fill = fill_soft
        capa.cell(i, 1).border = thin
        capa.merge_cells(start_row=i, start_column=2, end_row=i, end_column=6)
        capa.cell(i, 2, desc).font = font_body
        capa.cell(i, 2).border = thin
        capa.cell(i, 2).alignment = left

    capa["A22"] = "Totais de conferência (não biogênicos)"
    capa["A22"].font = font_section
    totals = [
        ("Scope 1", 1327.0),
        ("Scope 2", 2000.0),
        ("Scope 3", 4605.0),
        ("Total GEE", 7932.0),
        ("CO2 biogênico (fora do total)", 3690.0),
        ("Reduções já realizadas", 980.0),
    ]
    style_header_row(capa, 23, 1, 2)
    capa.cell(23, 1, "Indicador")
    capa.cell(23, 2, "tCO2e")
    for i, (label, value) in enumerate(totals, start=24):
        capa.cell(i, 1, label).font = font_total if "Total" in label else font_body
        capa.cell(i, 1).border = thin
        capa.cell(i, 1).fill = fill_gold if "Total" in label else fill_white
        cell = capa.cell(i, 2, value)
        cell.number_format = '#,##0.00'
        cell.font = font_total if "Total" in label else font_body
        cell.border = thin
        cell.alignment = center
        cell.fill = fill_gold if "Total" in label else fill_white

    capa.freeze_panes = "A4"
    capa.print_title_rows = "1:1"
    capa.sheet_properties.tabColor = GREEN

    # ---------------------------------------------------------- parte 1
    p1 = wb.create_sheet("Parte1_Informacoes")
    set_widths(p1, [36, 28, 22, 22, 22])
    p1.merge_cells("A1:D1")
    p1["A1"] = "Parte 1 — Informações gerais do inventário"
    p1["A1"].font = font_title
    p1.merge_cells("A2:D2")
    p1["A2"] = "Campos alinhados a inventory.description, inventorying_period_start/end e consolidation_approach."
    p1["A2"].font = font_muted

    fields = [
        ("Nome do inventário", "Inventário Fábrica Ponta Grossa 2025"),
        ("Descrição", "Inventário anual de emissões da unidade Fábrica Ponta Grossa"),
        ("Organização", "Vale Circular Celulose S.A."),
        ("Unidade", "Fábrica Ponta Grossa"),
        ("CNPJ da unidade", "11222333000144"),
        ("Setor / CNAE", "Fabricação de celulose e outras pastas — 1710-9/00"),
        ("Abordagem de consolidação", "Controle operacional"),
        ("Norma / programa", "GHG Protocol Corporate Standard + Programa Brasileiro GHG Protocol"),
        ("Ano de referência", 2025),
        ("Início do período", "2025-01-01"),
        ("Fim do período", "2025-12-31"),
        ("GWP utilizado", "AR5 (CO2=1, CH4=28, N2O=265, HFC-134a=1430)"),
        ("Unidade das emissões", "tCO2e (tonelada métrica de CO2 equivalente)"),
        ("Responsável técnico", "Responsável 10"),
        ("Validador interno", "Responsável 09"),
        ("Status no Aether", "INPUT / para análise"),
        ("Gases verdes já em uso", "Biogás capturado na ETE (captura ~70%); I-REC eólico em 30% da eletricidade."),
        ("Limitações", "Escopo 3 sem 'Uso de produtos vendidos'. Dados de fornecedor incompletos nas linhas E-08, E-12 e E-14."),
    ]
    p1["A4"] = "Campo"
    p1["B4"] = "Valor"
    style_header_row(p1, 4, 1, 4)
    p1.merge_cells("B4:D4")
    for i, (label, value) in enumerate(fields, start=5):
        kv(p1, i, label, value, fill_soft if i % 2 == 0 else fill_white)
        p1.row_dimensions[i].height = 22 if isinstance(value, (int, str)) and len(str(value)) < 80 else 36
    p1.freeze_panes = "A5"
    p1.sheet_properties.tabColor = GREEN

    headers = [
        "ID da fonte",
        "Atividade / fonte de emissão",
        "Unidade operacional",
        "Escopo",
        "Categoria",
        "Classificação (Scope 3)",
        "Gás",
        "Fórmula",
        "Quantidade (tCO2e)",
        "Metodologia",
        "% dados do fornecedor",
        "Biogênico",
        "Observações",
    ]

    def write_emission_sheet(ws, title, subtitle, rows, tab_color):
        set_widths(ws, [14, 46, 22, 12, 34, 22, 28, 12, 18, 30, 22, 12, 48])
        ws.merge_cells("A1:M1")
        ws["A1"] = title
        ws["A1"].font = font_title
        ws.merge_cells("A2:M2")
        ws["A2"] = subtitle
        ws["A2"].font = font_muted
        ws.row_dimensions[2].height = 36
        for col, header in enumerate(headers, start=1):
            ws.cell(4, col, header)
        style_header_row(ws, 4, 1, 13, fill_green_dark)
        start = 5
        for i, row in enumerate(rows):
            excel_row = start + i
            fill = fill_alt if i % 2 else fill_white
            for col, value in enumerate(row, start=1):
                cell = ws.cell(excel_row, col, value)
                cell.font = font_body
                cell.border = thin
                cell.fill = fill
                if col == 9:
                    cell.number_format = '#,##0.00'
                    cell.alignment = center
                elif col == 11:
                    cell.number_format = '0.0'
                    cell.alignment = center
                elif col in (2, 13):
                    cell.alignment = left
                else:
                    cell.alignment = center
            ws.row_dimensions[excel_row].height = 36
        total_row = start + len(rows)
        ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=8)
        ws.cell(total_row, 1, "TOTAL DA ABA")
        ws.cell(total_row, 1).font = font_total
        ws.cell(total_row, 1).fill = fill_gold
        ws.cell(total_row, 1).border = thin
        for col in range(2, 9):
            ws.cell(total_row, col).fill = fill_gold
            ws.cell(total_row, col).border = thin
        last_data = total_row - 1
        ws.cell(total_row, 9, f"=SUM(I{start}:I{last_data})")
        ws.cell(total_row, 9).number_format = '#,##0.00'
        ws.cell(total_row, 9).font = font_total
        ws.cell(total_row, 9).fill = fill_gold
        ws.cell(total_row, 9).border = thin
        ws.cell(total_row, 9).alignment = center
        for col in range(10, 14):
            ws.cell(total_row, col).fill = fill_gold
            ws.cell(total_row, col).border = thin
        # blank rows for the user to add more
        for extra in range(1, 6):
            r = total_row + extra
            for col in range(1, 14):
                cell = ws.cell(r, col, None)
                cell.border = thin
                cell.fill = fill_white
        ws.freeze_panes = "A5"
        ws.auto_filter.ref = f"A4:M{last_data}"
        ws.sheet_properties.tabColor = tab_color
        return start, last_data, total_row

    p2 = wb.create_sheet("Parte2_Emissoes")
    _, _, p2_total = write_emission_sheet(
        p2,
        "Parte 2 — Emissões de GEE (antropogênicas)",
        "Quantidade já em tCO2e. Cada linha vira um registro emission (is_reduction=false). "
        "Não invente ids: use somente os nomes do catálogo Aether.",
        EMISSIONS,
        GREEN,
    )

    p3 = wb.create_sheet("Parte3_Biogenicas")
    _, _, p3_total = write_emission_sheet(
        p3,
        "Parte 3 — Emissões biogênicas de CO2",
        "Reportadas separadamente, como no GHG Protocol. Não somam no total de GEE da aba Resumo. "
        "gas.is_biogenic = true (CO2(b)).",
        BIOGENIC,
        GOLD,
    )

    # ---------------------------------------------------------- reducoes
    red = wb.create_sheet("Reducoes")
    set_widths(red, [12, 56, 36, 22, 56])
    red.merge_cells("A1:E1")
    red["A1"] = "Reduções de emissões já realizadas"
    red["A1"].font = font_title
    red.merge_cells("A2:E2")
    red["A2"] = (
        "Cada linha vira um registro reduction (is_reduction=true): "
        "preencher quantity_co2e e category; gas, scope, methodology e "
        "supplier_data_percentage ficam nulos."
    )
    red["A2"].font = font_muted
    red.row_dimensions[2].height = 36
    for col, header in enumerate(
        ["ID", "Iniciativa", "Categoria", "Quantidade reduzida (tCO2e)", "Notas"],
        start=1,
    ):
        red.cell(4, col, header)
    style_header_row(red, 4, 1, 5)
    for i, row in enumerate(REDUCTIONS):
        excel_row = 5 + i
        fill = fill_alt if i % 2 else fill_white
        for col, value in enumerate(row, start=1):
            cell = red.cell(excel_row, col, value)
            cell.font = font_body
            cell.border = thin
            cell.fill = fill
            cell.alignment = left if col in (2, 5) else center
            if col == 4:
                cell.number_format = '#,##0.00'
        red.row_dimensions[excel_row].height = 32
    total_row = 5 + len(REDUCTIONS)
    red.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
    red.cell(total_row, 1, "TOTAL REDUZIDO")
    red.cell(total_row, 1).font = font_total
    red.cell(total_row, 1).fill = fill_gold
    red.cell(total_row, 1).border = thin
    for col in (2, 3):
        red.cell(total_row, col).fill = fill_gold
        red.cell(total_row, col).border = thin
    red.cell(total_row, 4, f"=SUM(D5:D{total_row - 1})")
    red.cell(total_row, 4).number_format = '#,##0.00'
    red.cell(total_row, 4).font = font_total
    red.cell(total_row, 4).fill = fill_gold
    red.cell(total_row, 4).border = thin
    red.cell(total_row, 5).fill = fill_gold
    red.cell(total_row, 5).border = thin
    for extra in range(1, 5):
        r = total_row + extra
        for col in range(1, 6):
            red.cell(r, col).border = thin
    red.freeze_panes = "A5"
    red.sheet_properties.tabColor = "2B6CB0"
    red_total = total_row

    # ---------------------------------------------------------- resumo
    resumo = wb.create_sheet("Resumo")
    set_widths(resumo, [42, 18, 18, 18, 18, 18])
    resumo.merge_cells("A1:C1")
    resumo["A1"] = "Resumo consolidado — Inventário Fábrica Ponta Grossa 2025"
    resumo["A1"].font = font_title
    resumo.merge_cells("A2:C2")
    resumo["A2"] = "Totais calculados a partir das abas Parte2, Parte3 e Reducoes. CO2 biogênico fica fora do total GEE."
    resumo["A2"].font = font_muted

    resumo["A4"] = "Por escopo (GEE antropogênico)"
    resumo["A4"].font = font_section
    style_header_row(resumo, 5, 1, 3)
    resumo.cell(5, 1, "Escopo")
    resumo.cell(5, 2, "tCO2e")
    resumo.cell(5, 3, "% do total")
    resumo["A6"] = "Scope 1"
    resumo["A7"] = "Scope 2"
    resumo["A8"] = "Scope 3"
    resumo["A9"] = "Total GEE"
    resumo["B6"] = '=SUMIF(Parte2_Emissoes!D:D,"Scope 1",Parte2_Emissoes!I:I)'
    resumo["B7"] = '=SUMIF(Parte2_Emissoes!D:D,"Scope 2",Parte2_Emissoes!I:I)'
    resumo["B8"] = '=SUMIF(Parte2_Emissoes!D:D,"Scope 3",Parte2_Emissoes!I:I)'
    resumo["B9"] = "=SUM(B6:B8)"
    resumo["C6"] = "=IF($B$9=0,0,B6/$B$9)"
    resumo["C7"] = "=IF($B$9=0,0,B7/$B$9)"
    resumo["C8"] = "=IF($B$9=0,0,B8/$B$9)"
    resumo["C9"] = "=1"
    for r in range(6, 10):
        resumo.cell(r, 1).font = font_total if r == 9 else font_body
        resumo.cell(r, 1).border = thin
        resumo.cell(r, 1).fill = fill_gold if r == 9 else fill_white
        resumo.cell(r, 2).number_format = '#,##0.00'
        resumo.cell(r, 2).font = font_total if r == 9 else font_body
        resumo.cell(r, 2).border = thin
        resumo.cell(r, 2).alignment = center
        resumo.cell(r, 2).fill = fill_gold if r == 9 else fill_white
        resumo.cell(r, 3).number_format = "0.0%"
        resumo.cell(r, 3).border = thin
        resumo.cell(r, 3).alignment = center
        resumo.cell(r, 3).fill = fill_gold if r == 9 else fill_white

    pie = PieChart()
    pie.title = "Participação por escopo"
    labels = Reference(resumo, min_col=1, min_row=6, max_row=8)
    data = Reference(resumo, min_col=2, min_row=5, max_row=8)
    pie.add_data(data, titles_from_data=True)
    pie.set_categories(labels)
    pie.dataLabels = DataLabelList()
    pie.dataLabels.showPercent = True
    pie.dataLabels.showVal = False
    pie.dataLabels.showCatName = True
    pie.width = 12
    pie.height = 8
    resumo.add_chart(pie, "E4")

    resumo["A11"] = "Por gás (Parte 2)"
    resumo["A11"].font = font_section
    style_header_row(resumo, 12, 1, 2)
    resumo.cell(12, 1, "Gás")
    resumo.cell(12, 2, "tCO2e")
    gases_resumo = [
        "Dióxido de carbono",
        "Metano",
        "Óxido nitroso",
        "Hidrofluorcarbonetos",
        "Hexafluoreto de enxofre",
    ]
    for i, gas in enumerate(gases_resumo):
        r = 13 + i
        resumo.cell(r, 1, gas).border = thin
        resumo.cell(r, 1).font = font_body
        resumo.cell(r, 2, f'=SUMIF(Parte2_Emissoes!G:G,A{r},Parte2_Emissoes!I:I)')
        resumo.cell(r, 2).number_format = '#,##0.00'
        resumo.cell(r, 2).border = thin
        resumo.cell(r, 2).alignment = center

    resumo["A19"] = "Fora do total GEE / já evitado"
    resumo["A19"].font = font_section
    style_header_row(resumo, 20, 1, 2)
    resumo.cell(20, 1, "Indicador")
    resumo.cell(20, 2, "tCO2e")
    extras = [
        ("CO2 biogênico (Parte 3)", "=SUM(Parte3_Biogenicas!I5:I50)"),
        ("Reduções já realizadas", "=SUM(Reducoes!D5:D50)"),
    ]
    for i, (label, formula) in enumerate(extras):
        r = 21 + i
        resumo.cell(r, 1, label).border = thin
        resumo.cell(r, 1).fill = fill_blue if i == 0 else fill_soft
        resumo.cell(r, 2, formula)
        resumo.cell(r, 2).number_format = '#,##0.00'
        resumo.cell(r, 2).border = thin
        resumo.cell(r, 2).alignment = center
        resumo.cell(r, 2).fill = fill_blue if i == 0 else fill_soft

    resumo["A24"] = "Pontos que o fluxo de análise deve enxergar"
    resumo["A24"].font = font_section
    notes = [
        "Maior fonte: Escopo 2 (eletricidade, 2.000 tCO2e) + Escopo 3 transporte upstream (1.800 tCO2e).",
        "CH4 residual de 450 tCO2e indica captura incompleta de biogás — oportunidade clara de gás verde.",
        "HFCs (85 tCO2e) e diesel da frota (192 tCO2e) são poluentes pontuais para o Analista de Poluentes.",
        "Já há biogás e I-REC: o Analista de Gases Verdes deve validar efetividade e o gap de captura.",
        "Reduções (980 tCO2e) não zeram o problema; o coordenador deve propor próximo passo com ROI.",
    ]
    for i, note in enumerate(notes):
        r = 25 + i
        resumo.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        resumo.cell(r, 1, f"• {note}").font = font_body
        resumo.cell(r, 1).alignment = left
        resumo.row_dimensions[r].height = 28
    resumo.sheet_properties.tabColor = GREEN_DARK

    # ---------------------------------------------------------- catalogo
    cat = wb.create_sheet("Catalogo")
    set_widths(cat, [10, 42, 18, 16, 14, 16])
    cat.merge_cells("A1:F1")
    cat["A1"] = "Catálogos auxiliares vigentes (seed Aether)"
    cat["A1"].font = font_title
    cat.merge_cells("A2:F2")
    cat["A2"] = "Use somente estes ids/nomes na extração estruturada. classification nula fora de Scope 3."
    cat["A2"].font = font_muted

    cat["A4"] = "Escopos"
    cat["A4"].font = font_section
    style_header_row(cat, 5, 1, 2)
    cat.cell(5, 1, "id")
    cat.cell(5, 2, "name")
    for i, name in enumerate(["Scope 1", "Scope 2", "Scope 3"], start=1):
        cat.cell(5 + i, 1, i).border = thin
        cat.cell(5 + i, 1).alignment = center
        cat.cell(5 + i, 2, name).border = thin

    cat["A10"] = "Categorias"
    cat["A10"].font = font_section
    style_header_row(cat, 11, 1, 3)
    cat.cell(11, 1, "id")
    cat.cell(11, 2, "name")
    cat.cell(11, 3, "classification")
    categories = [
        (1, "Combustão estacionária", None),
        (2, "Combustão móvel", None),
        (3, "Energia elétrica adquirida", None),
        (4, "Bens e serviços adquiridos", "UPSTREAM"),
        (5, "Bens de capital", "UPSTREAM"),
        (6, "Transporte e distribuição upstream", "UPSTREAM"),
        (7, "Resíduos gerados nas operações", "UPSTREAM"),
        (8, "Viagens a negócios", "UPSTREAM"),
        (9, "Deslocamento de funcionários", "UPSTREAM"),
        (10, "Transporte e distribuição downstream", "DOWNSTREAM"),
        (11, "Uso de produtos vendidos", "DOWNSTREAM"),
        (12, "Tratamento de fim de vida", "DOWNSTREAM"),
    ]
    for i, (cid, name, classification) in enumerate(categories):
        r = 12 + i
        cat.cell(r, 1, cid).border = thin
        cat.cell(r, 1).alignment = center
        cat.cell(r, 2, name).border = thin
        cell = cat.cell(r, 3, classification)
        cell.border = thin
        cell.alignment = center
        if classification == "UPSTREAM":
            cell.fill = fill_soft
        elif classification == "DOWNSTREAM":
            cell.fill = fill_blue

    cat["A26"] = "Gases"
    cat["A26"].font = font_section
    style_header_row(cat, 27, 1, 5)
    for col, header in enumerate(["id", "name", "formula", "is_biogenic", "gwp"], start=1):
        cat.cell(27, col, header)
    gases = [
        (1, "Dióxido de carbono", "CO2", False, 1),
        (2, "Metano", "CH4", False, 28),
        (3, "Óxido nitroso", "N2O", False, 265),
        (4, "Dióxido de carbono biogênico", "CO2(b)", True, 1),
        (5, "Hidrofluorcarbonetos", "HFCs", False, 1430),
        (6, "Hexafluoreto de enxofre", "SF6", False, 23500),
    ]
    for i, row in enumerate(gases):
        r = 28 + i
        for col, value in enumerate(row, start=1):
            cell = cat.cell(r, col, value)
            cell.border = thin
            cell.alignment = center if col != 2 else left
            cell.font = font_body
            if col == 4:
                cell.fill = fill_gold if value else fill_white

    cat["E4"] = "Listas de apoio"
    cat["E4"].font = font_section
    cat["E5"] = "Sim"
    cat["E6"] = "Não"
    cat["F5"] = "UPSTREAM"
    cat["F6"] = "DOWNSTREAM"
    for r in (5, 6):
        for col in (5, 6):
            cat.cell(r, col).border = thin
            cat.cell(r, col).alignment = center
    cat.sheet_properties.tabColor = MUTED

    for name, ref in (
        ("Escopos", "Catalogo!$B$6:$B$8"),
        ("Categorias", "Catalogo!$B$12:$B$23"),
        ("Gases", "Catalogo!$B$28:$B$33"),
        ("Biogenico", "Catalogo!$E$5:$E$6"),
        ("Classificacao", "Catalogo!$F$5:$F$6"),
    ):
        wb.defined_names.add(DefinedName(name=name, attr_text=ref))

    def add_list(ws, cell_range, named):
        dv = DataValidation(type="list", formula1=f"={named}", allow_blank=True)
        dv.error = "Use um valor do catálogo Aether."
        dv.errorTitle = "Valor fora do catálogo"
        dv.add(cell_range)
        ws.add_data_validation(dv)

    for sheet, end in ((p2, p2_total), (p3, p3_total)):
        add_list(sheet, f"D5:D{end + 5}", "Escopos")
        add_list(sheet, f"E5:E{end + 5}", "Categorias")
        add_list(sheet, f"F5:F{end + 5}", "Classificacao")
        add_list(sheet, f"G5:G{end + 5}", "Gases")
        add_list(sheet, f"L5:L{end + 5}", "Biogenico")
    add_list(red, f"C5:C{red_total + 4}", "Categorias")

    wb.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(path)
