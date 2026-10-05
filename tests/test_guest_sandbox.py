"""Local installed executor proof, no model/API calls or personal data reads."""
import base64
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from bridge.guest_sandbox import IsolatedExecutor

CODEX=Path('/usr/lib/chatgpt/resources/codex')

@unittest.skipUnless(sys.platform=='linux' and shutil.which('bwrap') and CODEX.is_file(),'Installed Linux executor required')
class SandboxTests(unittest.TestCase):
    def test_filesystem_namespace_and_workspace_write(self):
        with tempfile.TemporaryDirectory(prefix='rv-sandbox-proof-') as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            outside=root/'synthetic-owner-secret';outside.write_text('synthetic secret')
            (workspace/'hello.txt').write_text('synthetic workspace')
            (workspace/'outside-link').symlink_to(outside)
            executor=IsolatedExecutor(workspace,CODEX)
            try:
                read=executor.call('fs/readFile',{'path':'file:///workspace/hello.txt'})
                self.assertEqual(base64.b64decode(read['dataBase64']).decode(),'synthetic workspace')
                for path in (outside.as_uri(),'file:///workspace/outside-link','file:///home/wilmain/.codex/auth.json','file:///proc/1/root'+str(outside)):
                    with self.subTest(path=path):
                        with self.assertRaises(RuntimeError):executor.call('fs/readFile',{'path':path})
                executor.call('fs/writeFile',{'path':'file:///workspace/new.txt','dataBase64':base64.b64encode(b'guest result').decode()})
                self.assertEqual((workspace/'new.txt').read_text(),'guest result')
                self.assertEqual(outside.read_text(),'synthetic secret')
            finally:executor.close()

if __name__=='__main__':unittest.main()
