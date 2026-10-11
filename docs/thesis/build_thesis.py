"""Build the evidence-backed draft from this repository's restricted Markdown source.

Uses python-docx, Pillow and the Python standard library. No camera images are read.
This source format intentionally has only headings, paragraphs, tables and figure tags.
"""
import argparse
import gzip
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

HERE = Path(__file__).resolve().parent
FONT_PATH = Path('C:/Windows/Fonts/msyh.ttc')
BLACK, GRAY, TEAL, RED = '#111111', '#737373', '#16736a', '#b14343'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
                     + ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])


def font(size):
    return ImageFont.truetype(str(FONT_PATH), size)


def canvas(width, height):
    image = Image.new('RGB', (width, height), 'white')
    return image, ImageDraw.Draw(image)


def label(draw, xy, text, size=30, fill=BLACK, anchor=None):
    draw.text(xy, text, font=font(size), fill=fill, anchor=anchor)


def architecture(output):
    im, d = canvas(1600, 780)
    boxes = [(40, 80, 385, 220, 'USB 摄像头', '原生 720p MJPEG'),
             (470, 80, 850, 220, 'V4L2 采集进程', '驱动时间与发布时间'),
             (960, 80, 1555, 220, '最新帧共享内存', '一致性读取与计时扩展'),
             (40, 360, 385, 525, 'Linux 状态源', 'CPU / 温度 / 无线'),
             (470, 360, 850, 525, '只读观察服务', '1 Hz / 规则 / SQLite'),
             (960, 360, 1555, 525, '视频与时钟服务', '有界视频 / 独立时钟'),
             (470, 640, 1555, 755, '浏览器远程观测界面', '视频年龄 / 状态质量 / 事件前后证据')]
    for x1, y1, x2, y2, title, subtitle in boxes:
        d.rectangle((x1, y1, x2, y2), outline=BLACK, width=3)
        label(d, ((x1+x2)/2, y1+37), title, 35, anchor='mm')
        label(d, ((x1+x2)/2, y1+92), subtitle, 29, anchor='mm')
    def arrow(points):
        d.line(points, fill=BLACK, width=4)
        x, y = points[-1]; px, py = points[-2]
        if x == px: d.polygon([(x, y), (x-9, y-16), (x+9, y-16)], fill=BLACK)
        else: d.polygon([(x, y), (x-16, y-9), (x-16, y+9)], fill=BLACK)
    arrow([(385, 150), (470, 150)]); arrow([(850, 150), (960, 150)])
    arrow([(1255, 220), (1255, 360)])
    arrow([(1100, 220), (1100, 280), (660, 280), (660, 360)])
    label(d, (715, 298), '帧元数据与事件快照', 27)
    arrow([(385, 440), (470, 440)])
    arrow([(660, 525), (660, 640)]); arrow([(1255, 525), (1255, 640)])
    label(d, (685, 563), '状态与证据', 28); label(d, (1275, 563), 'JPEG 与计时', 28)
    label(d, (40, 690), '同一 Pi 单调时钟\n共享 Wi-Fi 非冗余', 27)
    im.save(output)


def grouped_bars(output, title, categories, series, ymax, unit):
    im, d = canvas(1500, 710)
    left, right, top, bottom = 135, 1440, 120, 565
    label(d, (left, 38), title, 36)
    for i in range(6):
        value = ymax*i/5; y = bottom - (bottom-top)*i/5
        d.line((left, y, right, y), fill='#dddddd', width=2)
        label(d, (left-18, y), f'{value:g}', 27, anchor='rm')
    label(d, (15, 84), unit, 28)
    colors = [TEAL, RED]
    group_width = (right-left)/len(categories)
    for i, category in enumerate(categories):
        cx = left + group_width*(i+.5)
        barwidth = min(100, group_width/4)
        for j, (name, values) in enumerate(series):
            x = cx + (j-(len(series)-1)/2)*(barwidth+8)
            y = bottom - (bottom-top)*values[i]/ymax
            d.rectangle((x-barwidth/2, y, x+barwidth/2, bottom), fill=colors[j])
            label(d, (x, y-15), f'{values[i]:.1f}', 27, anchor='mb')
        label(d, (cx, bottom+28), category, 29, anchor='mt')
    for j, (name, _) in enumerate(series):
        x = left + j*610
        d.rectangle((x, 660, x+24, 682), fill=colors[j]); label(d, (x+36, 652), name, 28)
    im.save(output)


def age_plot(output, raw):
    im, d = canvas(1500, 610)
    points = [(a['at']-raw['start'], a['age']) for a in raw['ages']]
    finite = [v/1000 for _, v in points if isinstance(v, (float, int))]
    ymax = max(1, math.ceil(max(finite)))
    duration = (raw['end']-raw['start'])/1000
    left, right, top, bottom = 130, 1440, 110, 490
    label(d, (left, 25), '完整监测页连续运行中的画面年龄', 36)
    label(d, (18, 80), '年龄 / s', 28)
    for i in range(6):
        y = bottom-(bottom-top)*i/5
        d.line((left, y, right, y), fill='#dddddd', width=2)
        label(d, (left-15, y), f'{ymax*i/5:.1f}', 27, anchor='rm')
    for i in range(7):
        x = left+(right-left)*i/6
        label(d, (x, bottom+20), f'{duration*i/6:.0f}', 27, anchor='mt')
    previous = None
    for t, value in points:
        if not isinstance(value, (int, float)):
            previous = None; continue
        pos = (left+(right-left)*t/1000/duration, bottom-(bottom-top)*value/1000/ymax)
        if previous: d.line((*previous, *pos), fill=TEAL, width=3)
        previous = pos
    label(d, (right, 565), '有效采样经过时间 / s', 28, anchor='rm')
    label(d, (left, 565), '100 ms 采样；校准失效处不连线', 26, fill=GRAY, anchor='lm')
    im.save(output)


def math_run(text):
    node = OxmlElement('m:r'); value = OxmlElement('m:t'); value.text = text; node.append(value); return node


def subscript(base, suffix):
    node = OxmlElement('m:sSub')
    for tag, text in [('m:e', base), ('m:sub', suffix)]:
        child = OxmlElement(tag); child.append(math_run(text)); node.append(child)
    return node


def fraction(numerator, denominator='2'):
    node = OxmlElement('m:f')
    for tag, values in [('m:num', numerator), ('m:den', [math_run(denominator)])]:
        child = OxmlElement(tag)
        for item in values: child.append(item)
        node.append(child)
    return node


def equation(doc, parts, number):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Pt(0)
    p.paragraph_format.space_before = Pt(8); p.paragraph_format.space_after = Pt(8)
    math = OxmlElement('m:oMath')
    for part in parts: math.append(math_run(part) if isinstance(part, str) else part)
    p._p.append(math); p.add_run('   ('+number+')')


def add_equations(doc, key):
    s = subscript
    if key == 'EQ_CLOCK_RTT': equation(doc, ['r = (', s('c','4'), ' − ', s('c','1'), ') − (', s('p','3'), ' − ', s('p','2'), ')'], '3-1')
    elif key == 'EQ_CLOCK_OFFSET': equation(doc, ['θ = ', fraction([math_run('('),s('p','2'),math_run(' − '),s('c','1'),math_run(') + ('),s('p','3'),math_run(' − '),s('c','4'),math_run(')')])], '3-2')
    elif key == 'EQ_SEGMENTS':
        equation(doc, [s('D','capture'),' = ',s('t','pub'),' − ',s('t','drv'),';   ',s('D','dispatch'),' = ',s('t','send'),' − ',s('t','pub')], '3-3')
        equation(doc, [s('D','network'),' = ',s('t','recv'),' + θ − ',s('t','send')], '3-4')
        equation(doc, [s('D','decode'),' = ',s('t','dec'),' − ',s('t','recv'),';   ',s('D','total'),' = ',s('t','dec'),' + θ − ',s('t','drv')], '3-5')
    elif key == 'EQ_AGE': equation(doc, ['A(c) = c + θ − ',s('t','pub,last')], '3-6')
    elif key == 'EQ_CORRELATION': equation(doc, ['Δ = ',s('t','event'),' − ',s('t','pub,snapshot')], '3-7')


def set_font(style, name='SimSun', size=12):
    style.font.name = name; style.font.size = Pt(size); style.font.color.rgb = RGBColor(0,0,0)
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn('w:rFonts'))
    if fonts is None: fonts = OxmlElement('w:rFonts'); rpr.append(fonts)
    for attribute in ('eastAsia', 'ascii', 'hAnsi'): fonts.set(qn('w:'+attribute), name)
    for attribute in ('eastAsiaTheme','asciiTheme','hAnsiTheme','cstheme'):
        fonts.attrib.pop(qn('w:'+attribute),None)
    for border in style.element.findall('.//'+qn('w:pBdr')): border.getparent().remove(border)


def add_table(doc, lines):
    rows = [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in lines]
    rows = [row for row in rows if not all(re.fullmatch(r':?-+:?', x) for x in row)]
    count = len(rows[0]); tab = doc.add_table(rows=0, cols=count)
    tab.alignment = WD_TABLE_ALIGNMENT.CENTER; tab.autofit = False
    widths = {3:[1.4,2.4,2.7], 4:[1.45,2.1,.85,2.1], 5:[1.35,.75,1.45,1.45,1.5], 6:[1.1,.65,1.15,1.15,1.25,1.2]}.get(count, [6.5/count]*count)
    for col, width in zip(tab.columns, widths): col.width = Inches(width)
    for index, values in enumerate(rows):
        cells = tab.add_row().cells
        trpr = tab.rows[-1]._tr.get_or_add_trPr(); trpr.append(OxmlElement('w:cantSplit'))
        if index == 0: trpr.append(OxmlElement('w:tblHeader'))
        for j, value in enumerate(values):
            cell = cells[j]; cell.width = Inches(widths[j]); cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tcpr = cell._tc.get_or_add_tcPr()
            margins = OxmlElement('w:tcMar')
            for side in ('top','left','bottom','right'):
                node = OxmlElement('w:'+side); node.set(qn('w:w'), '90'); node.set(qn('w:type'),'dxa'); margins.append(node)
            tcpr.append(margins)
            borders = OxmlElement('w:tcBorders')
            for side in ('top','left','bottom','right'):
                edge = OxmlElement('w:'+side); edge.set(qn('w:val'),'single'); edge.set(qn('w:sz'),'5'); edge.set(qn('w:color'),'D9D9D9'); borders.append(edge)
            tcpr.append(borders)
            shade = OxmlElement('w:shd'); shade.set(qn('w:fill'), 'E3E8EA' if index == 0 else 'FFFFFF'); tcpr.append(shade)
            p = cell.paragraphs[0]; p.paragraph_format.first_line_indent = Pt(0)
            p.paragraph_format.space_after = Pt(3); p.paragraph_format.space_before = Pt(3); p.paragraph_format.line_spacing = 1.15
            p.paragraph_format.keep_with_next = index < len(rows)-1
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if count >= 5 or j == 0 else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(value); run.font.size=Pt(10.5); run.bold=index==0
    p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(2); p.paragraph_format.line_spacing=1; p.paragraph_format.first_line_indent=Pt(0); p.add_run().font.size=Pt(2)


def build_doc(source, output, figures):
    doc = Document(); sec = doc.sections[0]
    sec.page_width=Inches(8.5); sec.page_height=Inches(11)
    sec.top_margin=Inches(.85); sec.bottom_margin=Inches(.85); sec.left_margin=Inches(1); sec.right_margin=Inches(1)
    sec.header_distance=Inches(.35); sec.footer_distance=Inches(.35); sec.different_first_page_header_footer=True
    for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Caption','Header','Footer']:
        set_font(doc.styles[name], 'SimSun', 12)
    for name in ['TOC Heading','toc 1','toc 2']:
        if name not in doc.styles: doc.styles.add_style(name,WD_STYLE_TYPE.PARAGRAPH)
        set_font(doc.styles[name],'SimSun',11)
        pf=doc.styles[name].paragraph_format
        pf.first_line_indent=Pt(0); pf.space_before=Pt(0); pf.space_after=Pt(3)
        pf.line_spacing=1.15; pf.keep_with_next=False; pf.page_break_before=False
    set_font(doc.styles['TOC Heading'],'SimHei',16)
    doc.styles['TOC Heading'].paragraph_format.page_break_before=True
    doc.styles['TOC Heading'].paragraph_format.space_after=Pt(12)
    normal=doc.styles['Normal'].paragraph_format
    normal.line_spacing=1.45; normal.first_line_indent=Pt(24); normal.space_after=Pt(5); normal.widow_control=True
    for name, size in [('Title',22),('Heading 1',16),('Heading 2',13)]:
        set_font(doc.styles[name], 'SimHei',size); doc.styles[name].font.bold=True
        form=doc.styles[name].paragraph_format; form.first_line_indent=Pt(0); form.keep_with_next=True
        form.space_before=Pt(14); form.space_after=Pt(10)
    doc.styles['Heading 1'].paragraph_format.page_break_before=True
    set_font(doc.styles['Caption'],'SimSun',10.5); doc.styles['Caption'].paragraph_format.first_line_indent=Pt(0)
    header=sec.header.paragraphs[0]; header.alignment=WD_ALIGN_PARAGRAPH.CENTER
    header.add_run('设备远程运维视频与状态协同监测    本科论文初稿').font.size=Pt(9)
    footer=sec.footer.paragraphs[0]; footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    field=OxmlElement('w:fldSimple'); field.set(qn('w:instr'),'PAGE'); footer._p.append(field)
    doc.core_properties.title='面向设备远程运维的视频与状态协同监测系统设计及实验研究'
    doc.core_properties.author=''; doc.core_properties.subject='基于真实部署与实验数据的本科毕业论文初稿'
    lines=source.splitlines(); i=0; cover=True
    while i<len(lines):
        line=lines[i].strip(); i+=1
        if not line: continue
        if line.startswith('|'):
            chunk=[line]
            while i<len(lines) and lines[i].strip().startswith('|'): chunk.append(lines[i]); i+=1
            add_table(doc,chunk); continue
        if line.startswith('{{EQ_'):
            add_equations(doc,line[2:-2]); continue
        if line.startswith('{{FIG_'):
            key=line[2:-2]; filename,caption=figures[key]
            p=doc.add_paragraph(); p.paragraph_format.first_line_indent=Pt(0); p.paragraph_format.keep_with_next=True
            p.add_run().add_picture(str(filename),width=Inches(6.45))
            p=doc.add_paragraph(caption,'Caption'); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
            continue
        if line.startswith('# '):
            title=line[2:]
            if cover:
                p=doc.add_paragraph(title,'Title'); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before=Pt(72); p.paragraph_format.space_after=Pt(40)
                cover=False; continue
            if title.startswith('第1章'):
                doc.add_paragraph('目录','TOC Heading')
                p=doc.add_paragraph(); p.paragraph_format.first_line_indent=Pt(0)
                p.paragraph_format.space_before=Pt(0); p.paragraph_format.space_after=Pt(0)
                p.paragraph_format.keep_with_next=False; p.paragraph_format.line_spacing=Pt(1)
                field=OxmlElement('w:fldSimple'); field.set(qn('w:instr'),'TOC \\o "1-2" \\h \\z \\u'); p._p.append(field)
            doc.add_heading(title,level=1); continue
        if line.startswith('## '): doc.add_heading(line[3:],level=2); continue
        if re.match(r'^表[0-9B]+-',line):
            p=doc.add_paragraph(line,'Caption'); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_with_next=True
            continue
        p=doc.add_paragraph(line)
        if line.startswith('[') and re.match(r'^\[\d+\]',line):
            p.paragraph_format.first_line_indent=Pt(0); p.paragraph_format.line_spacing=1.15
            for run in p.runs: run.font.size=Pt(10.5)
        if line.startswith(('本科毕业论文','专业：','作者、','实验日期：')):
            p.alignment=WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.first_line_indent=Pt(0); p.paragraph_format.space_after=Pt(16)
    doc.save(output)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--matrix',type=Path,required=True); parser.add_argument('--fault',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    figures_dir=args.output/'figures'; figures_dir.mkdir(exist_ok=True)
    reports=json.loads((args.matrix/'summary.json').read_text(encoding='utf-8'))
    if len(reports)!=5: raise ValueError('All four comparisons and HMI run are required')
    fault=json.loads(gzip.decompress(args.fault.read_bytes()).decode('utf-8') if args.fault.suffix=='.gz' else args.fault.read_text(encoding='utf-8'))
    for run in fault['runs']:
        assert run['observer_stayed_good']
        assert {e['phase'] for e in run['evidence']}=={'ACTIVE','RECOVERED'}
        assert all(e['context_complete'] and len(e['after'])==5 for e in run['evidence'])
    a=reports[:4]; h=reports[4]
    substitutions={
      'ABSTRACT_RESULTS':f"共享与独立时钟的四轮对照累计观察 {sum(r['frameCount'] for r in a)} 个成功解码帧，但无线链路仍出现秒级长尾，未证实独立时钟能稳定降低视频交付时延。",
      'MATRIX_TABLE':table(['轮次','fps','均值 ms','P95 ms','年龄 P95 ms','间隔 >1 s'],[[r['label'],f"{r['fps']:.2f}",f"{r['latency']['mean']:.1f}",f"{r['latency']['p95']:.1f}",f"{r['age']['p95']:.1f}",r['gaps1000']] for r in a]),
      'MATRIX_DISCUSSION':f"四轮实际持续 {sum(r['duration'] for r in a):.3f} s，成功解码 {sum(r['frameCount'] for r in a)} 帧，尺寸均为 1280×720，浏览器脚本异常和负发布至解码样本均为零。独立连接两轮的发布至解码均值分别为 {a[1]['latency']['mean']:.1f} ms 和 {a[2]['latency']['mean']:.1f} ms，而共享连接两轮为 {a[0]['latency']['mean']:.1f} ms 和 {a[3]['latency']['mean']:.1f} ms。轮次变化大于可以稳定归因的收益：既有独立通道优于共享的轮次，也有共享 A2 更好的情况。四轮共记录 {sum(r['gaps1000'] for r in a)} 次超过 1 s 的相邻解码间隔；状态请求非 GOOD 或失败合计 {sum(r['statusBad'] for r in a)} 次。不能据此宣布长尾已解决。",
      'SOAK_RESULTS':f"连续运行的实际采样时间为 {h['duration']:.3f} s，共解码 {h['frameCount']} 帧，平均 {h['fps']:.2f} fps。发布至解码估计均值为 {h['latency']['mean']:.1f} ms、P95 为 {h['latency']['p95']:.1f} ms；驱动至解码均值为 {h['driverToDecode']['mean']:.1f} ms、P95 为 {h['driverToDecode']['p95']:.1f} ms。画面年龄 P95 为 {h['age']['p95']:.1f} ms，最大有效年龄为 {h['age']['max']:.1f} ms；{h['ageInvalid']} 次年龄采样没有有效校准值。相邻解码间隔超过 200 ms 共 {h['gaps200']} 次，超过 1 s 共 {h['gaps1000']} 次。状态请求共 {h['statusRequests']} 次，其中非 GOOD 或失败 {h['statusBad']} 次。整机 CPU 有效样本均值为 {h['cpu']['mean']:.2f}%，温度最大值 {h['temperature']['max']:.2f}°C，采样迟到 P95 为 {h['samplingLate']['p95']:.2f} ms。以上是当前环境的真实观察结果，不构成长时间稳定性承诺。",
      'FAULT_TABLE':table(['轮次','触发时间 ms','恢复时间 ms','状态通道'],[[r['index'],f"{r['alarm_detection_ms']:.1f}",f"{r['recovery_detection_ms']:.1f}",'持续 GOOD'] for r in fault['runs']])}
    source=(HERE/'thesis-template.md').read_text(encoding='utf-8')
    for key,value in substitutions.items(): source=source.replace('{{'+key+'}}',value)
    remaining=set(re.findall(r'\{\{(.*?)\}\}',source))
    assert all(k.startswith(('EQ_','FIG_')) for k in remaining),remaining
    architecture(figures_dir/'architecture.png')
    grouped_bars(figures_dir/'matrix.png','共享与独立时钟对照的成功帧统计',[r['label'] for r in a],
                 [('发布至解码均值',[r['latency']['mean'] for r in a]),('发布至解码 P95',[r['latency']['p95'] for r in a])],150,'ms')
    grouped_bars(figures_dir/'fault.png','三轮真实采集暂停的规则响应',[f"第 {r['index']} 轮" for r in fault['runs']],
                 [('告警触发',[r['alarm_detection_ms']/1000 for r in fault['runs']]),('恢复确认',[r['recovery_detection_ms']/1000 for r in fault['runs']])],3,'s')
    with gzip.open(args.matrix/'C-hmi-soak.json.gz','rt',encoding='utf-8') as stream: raw=json.load(stream)['raw']
    age_plot(figures_dir/'age.png',raw)
    figures={'FIG_ARCHITECTURE':(figures_dir/'architecture.png','图 2-1 系统职责与逻辑通道'),
             'FIG_MATRIX':(figures_dir/'matrix.png','图 6-1 相同画质下四轮成功帧统计  不含未交付帧的等待代价'),
             'FIG_AGE':(figures_dir/'age.png','图 6-2 完整监测页的画面年龄  来源 C-hmi-soak.json.gz'),
             'FIG_FAULT':(figures_dir/'fault.png','图 6-3 从暂停或恢复确认到规则事件的时间')}
    build_doc(source,args.output/'thesis-draft.docx',figures)
    # Readable repository source retains mathematical text and relative figure links.
    math_text={'EQ_CLOCK_RTT':'r = (c4 - c1) - (p3 - p2)', 'EQ_CLOCK_OFFSET':'theta = ((p2 - c1) + (p3 - c4)) / 2',
               'EQ_SEGMENTS':'D_capture = t_pub - t_drv; D_dispatch = t_send - t_pub; D_network = t_recv + theta - t_send; D_decode = t_dec - t_recv; D_total = t_dec + theta - t_drv',
               'EQ_AGE':'A(c) = c + theta - t_pub,last','EQ_CORRELATION':'delta = t_event - t_pub,snapshot'}
    for key,value in math_text.items(): source=source.replace('{{'+key+'}}','```text\n'+value+'\n```')
    for key,(filename,caption) in figures.items(): source=source.replace('{{'+key+'}}',f'![{caption}](figures/{filename.name})\n\n{caption}')
    (args.output/'thesis.md').write_text(source,encoding='utf-8')
    (args.output/'build-report.json').write_text(json.dumps({'characters':len(source),'chinese_characters':len(re.findall(r'[\u4e00-\u9fff]',source)),
        'matrix_frames':sum(r['frameCount'] for r in reports),'matrix_seconds':sum(r['duration'] for r in reports),'references':10,'equations':7,'figures':4,'draft':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print((args.output/'build-report.json').read_text(encoding='utf-8'))


if __name__=='__main__': main()
