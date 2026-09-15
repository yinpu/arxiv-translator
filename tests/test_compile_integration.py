"""Real local TeX tests. Run after installing TeX Live/MacTeX.

ARXIV_TEST_OUTPUT_DIR retains PDFs/logs for visual inspection when set.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'arxiv-translator/scripts/compile.py'
TEX_PATH = os.environ.get('PATH', os.defpath) + os.pathsep + '/Library/TeX/texbin'
BIB = '@book{knuth, author={Donald E. Knuth}, title={The TeXbook}, year={1984}, publisher={Addison-Wesley}}\n'
BODY = r'''
\section{本地编译验证}\label{sec:first}
中文正文与数学公式：$E=mc^2$，以及 $\sum_{i=1}^{n} i = n(n+1)/2$。
\textbf{中文粗体}，\textit{中文斜体}，{\sffamily 中文无衬线}，\texttt{中文等宽}。
见第~\ref{sec:second}~节。图~\ref{fig:test}~展示本地 PDF 图片。
\begin{figure}[h]
\centering\includegraphics[width=3cm]{figures/test figure.pdf}
\caption{本地图片资源}\label{fig:test}
\end{figure}
\input{sections/second}
'''


def figure_pdf():
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 60] /Resources << >> /Contents 4 0 R >>']
    stream = b'0.1 0.4 0.8 rg 10 10 100 40 re f\n'
    objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream')
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(pdf)
    pdf.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        pdf.extend(f'{offset:010d} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(pdf)


@unittest.skipUnless(shutil.which('latexmk', path=TEX_PATH), 'requires local TeX Live/MacTeX')
class RealCompilationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        retained = os.environ.get('ARXIV_TEST_OUTPUT_DIR')
        if retained:
            cls.root = Path(retained).resolve()
            cls.root.mkdir(parents=True, exist_ok=True)
        else:
            cls.temp = tempfile.TemporaryDirectory(prefix='arxiv-local-tex-')
            cls.addClassCleanup(cls.temp.cleanup)
            cls.root = Path(cls.temp.name)
        cls.env = os.environ.copy()
        cls.env['TEXMFVAR'] = str(cls.root / 'tex-cache')
        cls.env['TEXMFCACHE'] = cls.env['TEXMFVAR']
        cls.env['PYTHONDONTWRITEBYTECODE'] = '1'

    def setUp(self):
        self.work = self.root / self._testMethodName / '中文 project'
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / 'sub dir').mkdir(exist_ok=True)
        (self.work / 'sections').mkdir(exist_ok=True)
        (self.work / 'figures').mkdir(exist_ok=True)
        (self.work / 'sections/second.tex').write_text(r'\section{第二节}\label{sec:second}回看第~\ref{sec:first}~节。', encoding='utf-8')
        (self.work / 'figures/test figure.pdf').write_bytes(figure_pdf())
        self.main = self.work / 'sub dir/中文 main.tex'
        self.pdf = self.work / '输出目录/中文 main.pdf'

    def source(self, extra='', ending=''):
        self.main.write_text('\\documentclass{article}\n\\usepackage{graphicx}\n' + extra + '\n\\begin{document}\n' + BODY + ending + '\n\\end{document}\n', encoding='utf-8')

    def run_compile(self, success=True, output=None):
        command = [sys.executable, str(SCRIPT), str(self.work), str(self.main), output or str(self.pdf)]
        result = subprocess.run(command, capture_output=True, text=True, env=self.env, timeout=960)
        (self.work / 'test-driver.log').write_text(result.stdout + result.stderr)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr[-10000:])
            self.assertTrue(self.pdf.read_bytes().startswith(b'%PDF-'))
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_chinese_nested_paths_repeat_and_failure_protection(self):
        self.source(extra='\\usepackage[T1]{fontenc}\n\\usepackage[utf8]{inputenc}')
        self.run_compile(output=str(self.pdf.parent) + '/')
        once = self.main.read_bytes()
        self.run_compile(output=str(self.pdf.parent))
        self.assertEqual(self.main.read_bytes(), once)
        old = hashlib.sha256(self.pdf.read_bytes()).digest()
        self.main.write_text(self.main.read_text().replace('\\end{document}', '\\unknownArxivCommand\n\\end{document}'))
        self.run_compile(success=False)
        self.assertEqual(hashlib.sha256(self.pdf.read_bytes()).digest(), old)

    def test_prebuilt_bbl_without_bib(self):
        self.source(ending='\n参考文献~\\cite{knuth}。\n\\bibliographystyle{plain}\n\\bibliography{missing}\n')
        (self.work / '中文 main.bbl').write_text('\\begin{thebibliography}{1}\n\\bibitem{knuth} Donald E. Knuth. The TeXbook. 1984.\n\\end{thebibliography}\n')
        self.run_compile()

    def test_bibtex(self):
        self.source(ending='\n参考文献~\\cite{knuth}。\n\\bibliographystyle{plain}\n\\bibliography{references}\n')
        (self.work / 'references.bib').write_text(BIB)
        self.run_compile()
        self.assertTrue(list((self.work / '.arxiv-build').glob('run-*/attempt-1/*.blg')))

    def test_biber_and_prebuilt_without_bib(self):
        self.source(extra='\\usepackage[backend=biber]{biblatex}\n\\addbibresource{references.bib}', ending='\n参考文献~\\cite{knuth}。\n\\printbibliography\n')
        bib = self.work / 'references.bib'
        bib.write_text(BIB)
        self.run_compile()
        bbl = next((self.work / '.arxiv-build').glob('run-*/attempt-1/*.bbl'))
        shipped = self.work / 'sub dir/中文 main.bbl'
        shutil.copyfile(bbl, shipped)
        before = shipped.read_bytes()
        bib.unlink()
        self.run_compile()
        self.assertEqual(shipped.read_bytes(), before)

    def test_xelatex_existing_cjk(self):
        self.source(extra='\\usepackage[AutoFakeBold]{xeCJK}\n\\setCJKmainfont{FandolSong-Regular.otf}\n\\setCJKsansfont{FandolHei-Regular.otf}\n\\setCJKmonofont{FandolFang-Regular.otf}')
        before = self.main.read_bytes()
        result = self.run_compile()
        self.assertIn('Local xelatex', result.stderr)
        self.assertEqual(self.main.read_bytes(), before)

    def test_macro_auto_repair_and_unresolved_citation(self):
        self.source(extra='\\newcommand{\\LaTeX}{Local TeX}')
        result = self.run_compile()
        self.assertIn('attempt 2/3', result.stderr)
        old = self.pdf.read_bytes()
        self.main.write_text(self.main.read_text().replace('\\end{document}', '\\cite{does-not-exist}\n\\end{document}'))
        self.run_compile(success=False)
        self.assertEqual(self.pdf.read_bytes(), old)


if __name__ == '__main__':
    unittest.main()
