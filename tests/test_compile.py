"""Behavioral regression tests; no TeX installation or network required."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'arxiv-translator/scripts/compile.py'
spec = importlib.util.spec_from_file_location('local_compile', SCRIPT)
compiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compiler)
PDF = b'%PDF-1.4\n% test output\n%%EOF\n'


class LocalCompileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='arxiv-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / '中文 project'
        self.work.mkdir()
        self.main = self.work / 'sub dir/main file.tex'
        self.main.parent.mkdir()
        self.main.write_text('\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n')
        self.output = self.work / 'main file.pdf'
        self.output.write_bytes(b'old-pdf-must-survive')

    def compile(self):
        return compiler.compile_local(str(self.work), 'sub dir/main file.tex', str(self.output))

    def fake_success(self, command, cwd, env, log_path):
        self.assertEqual(command[-1], './sub dir/main file.tex')
        self.assertIn('-norc', command)
        self.assertIn('-no-shell-escape', command)
        self.assertIn('-lualatex', command)
        self.assertFalse((Path(cwd) / '.arxiv-build').exists())
        self.assertTrue((Path(cwd) / 'sub dir/main file.tex').exists())
        self.assertFalse((Path(cwd) / 'main file.pdf').exists())
        (Path(cwd) / 'main file.pdf').write_bytes(PDF)
        (Path(cwd) / 'main file.log').write_text('Output written on main file.pdf.\n')
        Path(log_path).write_text('Full successful output\n')
        return 0

    def test_success_preserves_resources_and_publishes_atomically(self):
        (self.work / 'unusual figure.pdf').write_bytes(PDF)
        (self.work / 'main file.aux').write_text('stale aux')
        with patch.object(compiler, '_require_tool', side_effect=lambda n, e: n), \
             patch.object(compiler, '_run_latexmk', side_effect=self.fake_success):
            self.assertTrue(self.compile())
        self.assertEqual(self.output.read_bytes(), PDF)
        stage = next((self.work / '.arxiv-build').glob('run-*/attempt-1'))
        self.assertEqual((stage / 'unusual figure.pdf').read_bytes(), PDF)
        self.assertFalse((stage / 'main file.aux').exists())

    def test_missing_tool_does_not_change_sources_or_old_pdf(self):
        before = self.main.read_bytes()
        with patch.object(compiler.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Missing local tool: latexmk'):
                self.compile()
        self.assertEqual(self.main.read_bytes(), before)
        self.assertEqual(self.output.read_bytes(), b'old-pdf-must-survive')
        self.assertFalse((self.work / '.arxiv-build').exists())

    def test_compile_error_preserves_pdf_and_log(self):
        def fail(command, cwd, env, log_path):
            Path(log_path).write_text('Undefined control sequence: test failure')
            return 1
        with patch.object(compiler, '_require_tool', side_effect=lambda n, e: n), \
             patch.object(compiler, '_run_latexmk', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Full log:'):
                self.compile()
        self.assertEqual(self.output.read_bytes(), b'old-pdf-must-survive')
        self.assertEqual(len(list((self.work / '.arxiv-build').glob('run-*/attempt-*.log'))), 1)

    def test_timeout_preserves_old_pdf(self):
        with patch.object(compiler, '_require_tool', side_effect=lambda n, e: n), \
             patch.object(compiler, '_run_latexmk', side_effect=subprocess.TimeoutExpired('latexmk', 300)):
            with self.assertRaisesRegex(RuntimeError, 'timed out after 300s'):
                self.compile()
        self.assertEqual(self.output.read_bytes(), b'old-pdf-must-survive')

    def test_real_subprocess_timeout_reaps_child(self):
        pid_file = self.root / 'pid'
        command = [sys.executable, '-c',
                   'import os,time; from pathlib import Path; '
                   f'Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)']
        with self.assertRaises(subprocess.TimeoutExpired):
            compiler._run_latexmk(command, str(self.root), os.environ.copy(), str(self.root / 'timeout.log'), timeout=0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_bad_or_unresolved_pdf_never_published(self):
        cases = [('bad pdf', 'fine'), ('%PDF-1.4\nno eof', 'fine'),
                 (PDF.decode(), 'LaTeX Warning: There were undefined references.'),
                 (PDF.decode(), "Package natbib Warning: Citation `missing' on page 1 undefined on input line 4."),
                 (PDF.decode(), 'Missing character: There is no 中 in font test!')]
        for pdf, log in cases:
            with self.subTest(log=log):
                def produce(command, cwd, env, log_path):
                    Path(log_path).write_text('Successful engine return')
                    (Path(cwd) / 'main file.pdf').write_text(pdf)
                    (Path(cwd) / 'main file.log').write_text(log)
                    return 0
                with patch.object(compiler, '_require_tool', side_effect=lambda n, e: n), \
                     patch.object(compiler, '_run_latexmk', side_effect=produce):
                    with self.assertRaises(RuntimeError):
                        self.compile()
                self.assertEqual(self.output.read_bytes(), b'old-pdf-must-survive')

    def test_macro_repair_retries_and_stops(self):
        self.main.write_text('\\documentclass{article}\n\\newcommand{\\foo}{bar}\n\\begin{document}Hello\\end{document}')
        calls = []
        def run(command, cwd, env, log_path):
            calls.append(command)
            if len(calls) == 1:
                Path(log_path).write_text('./sub dir/main file.tex:2: LaTeX Error: Command \\foo already defined\n')
                return 1
            return self.fake_success(command, cwd, env, log_path)
        with patch.object(compiler, '_require_tool', side_effect=lambda n, e: n), \
             patch.object(compiler, '_run_latexmk', side_effect=run):
            self.compile()
        self.assertEqual(len(calls), 2)
        self.assertIn('\\renewcommand{\\foo}', self.main.read_text())

    def test_prebuilt_bbl_reused_and_idempotent(self):
        self.main.write_text('\\documentclass{article}\n\\begin{document}\n\\cite{entry}\n\\bibliography{missing}\n\\end{document}')
        bbl = '\\begin{thebibliography}{1}\n\\bibitem{entry} Author. Title.\n\\end{thebibliography}'
        (self.work / 'main file.bbl').write_text(bbl)
        self.assertTrue(compiler._inline_prebuilt_bbl(str(self.work), 'sub dir/main file.tex'))
        once = self.main.read_text()
        self.assertIn(bbl, once)
        self.assertFalse(compiler._inline_prebuilt_bbl(str(self.work), 'sub dir/main file.tex'))
        self.assertEqual(self.main.read_text(), once)

    def test_biblatex_prebuilt_bbl_preserved_without_bib(self):
        self.main.write_text('\\usepackage [backend=biber] {biblatex}\n\\addbibresource[location=local]{missing.bib}')
        bbl = self.work / 'sub dir/main file.bbl'
        bbl.write_text('biblatex bbl sentinel')
        self.assertFalse(compiler._inline_prebuilt_bbl(str(self.work), 'sub dir/main file.tex'))
        self.assertEqual(compiler._bibliography_option(str(self.work), 'sub dir/main file.tex'), '-bibtex-')
        self.assertEqual((self.work / 'main file.bbl').read_text(), bbl.read_text())
        (self.work / 'missing.bib').write_text('@article{test,title={Test}}')
        self.assertEqual(compiler._bibliography_option(str(self.work), 'sub dir/main file.tex'), '-bibtex-cond')

    def test_cjk_injection_idempotent_and_respects_existing_stack(self):
        self.main.write_text('% \\begin{document}\n\\documentclass{article}\n\\begin{document}中文\\end{document}')
        self.assertTrue(compiler._ensure_cjk_support(str(self.work), 'sub dir/main file.tex'))
        once = self.main.read_text()
        self.assertIn('FandolSong-Regular.otf', once)
        self.assertFalse(compiler._ensure_cjk_support(str(self.work), 'sub dir/main file.tex'))
        self.assertEqual(self.main.read_text(), once)
        self.main.write_text('\\documentclass{article}\n\\usepackage[AutoFakeBold]{xeCJK}\n\\begin{document}中文\\end{document}')
        self.assertEqual(compiler._detect_compiler(str(self.work), 'sub dir/main file.tex'), 'xelatex')
        self.assertFalse(compiler._ensure_cjk_support(str(self.work), 'sub dir/main file.tex'))

    def test_main_outside_work_rejected_and_directory_output_supported(self):
        outside = self.root / 'outside.tex'
        outside.write_text('outside')
        with self.assertRaises(ValueError):
            compiler._main_tex_relative(str(self.work), str(outside))
        (self.work / 'linked.tex').symlink_to(outside)
        with self.assertRaises(ValueError):
            compiler._main_tex_relative(str(self.work), 'linked.tex')
        self.assertEqual(compiler._resolve_output_pdf(str(self.root) + '/new 中文/', 'sub dir/main file.tex'), str(self.root / 'new 中文/main file.pdf'))

    def test_failed_publish_preserves_existing_file(self):
        source = self.root / 'new.pdf'
        source.write_bytes(PDF)
        with patch.object(compiler.shutil, 'copyfile', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                compiler._publish_pdf(source, self.output)
        self.assertEqual(self.output.read_bytes(), b'old-pdf-must-survive')
        self.assertFalse(list(self.work.glob('.arxiv-pdf-*')))

    def test_cleanup_backup_excludes_build_copies(self):
        cleanup_spec = importlib.util.spec_from_file_location('cleanup', SCRIPT.with_name('cleanup.py'))
        cleanup = importlib.util.module_from_spec(cleanup_spec)
        cleanup_spec.loader.exec_module(cleanup)
        stage = self.work / '.arxiv-build/run-test/attempt-1'
        stage.mkdir(parents=True)
        (stage / 'main.tex').write_text('obsolete build copy')
        backup = self.root / 'backup'
        copied = cleanup.backup_translated_sources(str(self.work), str(backup))
        self.assertEqual(len(copied), 1)
        self.assertEqual((backup / 'sub dir/main file.tex').read_bytes(), self.main.read_bytes())
        self.assertFalse((backup / '.arxiv-build').exists())


if __name__ == '__main__':
    unittest.main()
