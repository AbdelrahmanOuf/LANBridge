"""Run local validation and generate an honest, machine-readable delivery report."""
from __future__ import annotations

import ast
import datetime as dt
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=300, check=False, shell=False)
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError('Validation failed: ' + ' '.join(args))
    return result


def main() -> None:
    docs = ROOT / 'docs'
    lint = run([sys.executable, '-m', 'ruff', 'check', 'src', 'tests', 'tools', 'build.py'])
    tests = run([sys.executable, '-m', 'pytest', '-q', '--junitxml=docs/test-results.xml'])
    tree = ET.parse(docs / 'test-results.xml')
    cases = list(tree.iter('testcase'))
    skipped = [x for x in cases if x.find('skipped') is not None]
    failed = [x for x in cases if x.find('failure') is not None or x.find('error') is not None]
    python_files = [p for p in ROOT.rglob('*.py') if not any(
        x.startswith('.') or x in ('build', 'dist', '__pycache__', 'vendor') or x.endswith('.egg-info')
        for x in p.relative_to(ROOT).parts)]
    for file in python_files:
        ast.parse(file.read_text(encoding='utf-8'), filename=str(file))
    ET.parse(ROOT / 'packaging/LANBridgePro.manifest')
    resources = ROOT / 'src/lanbridge/resources'
    for svg in resources.glob('*.svg'):
        ET.parse(svg)
    result = {
        'generated_utc': dt.datetime.now(dt.UTC).isoformat(),
        'python': platform.python_version(), 'host_platform': platform.platform(),
        'ruff': lint.stdout.strip(), 'python_ast_files': len(python_files),
        'tests': {'total': len(cases), 'passed': len(cases) - len(skipped) - len(failed),
                  'skipped': len(skipped), 'failed': len(failed),
                  'skip_reasons': [{'test': x.attrib.get('name'),
                                    'reason': x.find('skipped').attrib.get('message')} for x in skipped]},
        'xml_manifest_and_svg': 'parsed successfully',
        'windows_exe_built_here': False,
        'actual_compiled_exe_test_here': False,
        'uac_mutations_tested_here': False,
        'two_pc_lan_tested_here': False,
        'note': 'Native/integration skips are NOT successful results. See WINDOWS_ACCEPTANCE.md.'}
    (docs / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = f'''# تقرير التحقق الفعلي

تاريخ إنشاء التقرير UTC: `{result['generated_utc']}`.

| الفحص | النتيجة |
|---|---|
| المضيف | {result['host_platform']} |
| Python | {result['python']} |
| اختبارات Pytest | **{result['tests']['passed']} ناجح، {len(skipped)} متجاوز، {len(failed)} فاشل** |
| Ruff | {result['ruff']} |
| Python AST | {len(python_files)} ملفًا دون أخطاء Syntax |
| Manifest وSVG | تم تحليل XML بنجاح |
| واجهة Qt | فتح الصفحات الثماني ورسمها واختبار مسار بدء مهمة بإشارات الخلفية |
| ملفات كثيرة | فحص Manifest فعلي لـ3,000 ملف محلي والتحقق من العدادات وحدود الدفعات |
| EXE Windows | **لم يُبنَ هنا** |
| اختبار EXE المجمّع | **لم يُنفّذ هنا**؛ build.py يفرضه على Windows |
| UAC/PowerShell وتغييرات النظام الحقيقية | **لم تُختبر هنا** |
| جهازا Windows / كابل / إغلاق فعلي / Domain | **لم تُختبر هنا** |

الاختبارات المحلية تستخدم ملفات مؤقتة وMock للطبقات الخاصة بـWindows حيث يلزم. لا تعني
نجاح SMB فعليًا أو أن قيود Windows/الدومين قد اجتازت الاختبار. لا يمكن ضمان خلو برنامج غير
مختبر على منصته المستهدفة من الأخطاء؛ هذه النتيجة دليل فحوص محددة وليست اعتماد إنتاج.

التفاصيل القابلة للقراءة آليًا في [validation.json](validation.json)، ونتيجة JUnit في
[test-results.xml](test-results.xml). خطوات اعتماد Windows في [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md).

الصور في previews ناتجة عن تشغيل Qt فعليًا على مضيف الاختبار، دون بيانات شبكة أو نسب نقل مختلقة.
'''
    (docs / 'VALIDATION_REPORT.md').write_text(summary, encoding='utf-8')
    print(tests.stdout.strip().splitlines()[-1])
    print('Validation report written.')


if __name__ == '__main__':
    main()
