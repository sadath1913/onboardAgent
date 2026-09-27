import tempfile
import unittest
from pathlib import Path

from backend import config
from backend.database import connect, initialize
from backend.retrieval import search_repository


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = config.settings.data_dir
        object.__setattr__(config.settings, "data_dir", Path(self.temp.name))
        initialize()
        with connect() as db:
            db.execute("INSERT INTO repositories(id,owner,name,url,status,created_at,updated_at) VALUES('repo','octo','demo','https://github.com/octo/demo.git','completed','now','now')")
            db.execute("INSERT INTO files(repository_id,path,kind,size,sha256,line_count,content) VALUES('repo','app/auth.py','source',20,'hash',3,'def validate_token(token):\\n    return token == \\\"ok\\\"\\n')")
            db.execute("INSERT INTO files(repository_id,path,kind,size,sha256,line_count,content) VALUES('repo','app/routes.py','source',15,'hash2',2,'from app.auth import validate_token\\nroute = validate_token')")
            db.execute("INSERT INTO files(repository_id,path,kind,size,sha256,line_count,content) VALUES('repo','app/handlers.py','source',15,'hash3',2,'def build_response():\\n    return {}')")
            db.execute("INSERT INTO symbols(repository_id,symbol_key,file_path,name,qualified_name,kind,start_line,end_line,signature,docstring) VALUES('repo','app/auth.py::validate_token:1','app/auth.py','validate_token','validate_token','function',1,2,'validate_token(token)','Check token')")
            db.execute("INSERT INTO relationships(repository_id,source_type,source_key,target_type,target_key,relation,evidence,line_number) VALUES('repo','file','app/routes.py','file','app/auth.py','imports','from app.auth import validate_token',1)")
            db.execute("INSERT INTO relationships(repository_id,source_type,source_key,target_type,target_key,relation,evidence,line_number) VALUES('repo','file','app/handlers.py','file','app/auth.py','imports','from app.auth import validate_token',1)")

    def tearDown(self):
        object.__setattr__(config.settings, "data_dir", self.old_data_dir)
        self.temp.cleanup()

    def test_symbol_search_returns_source_lines_and_related_file(self):
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM files WHERE repository_id='repo'").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM symbols WHERE repository_id='repo'").fetchone()[0], 1)
        results = search_repository("repo", "where is validate token checked", 8)
        self.assertTrue(results)
        self.assertEqual(results[0]["path"], "app/auth.py")
        self.assertGreaterEqual(results[0]["start_line"], 1)
        self.assertTrue(any(item["path"] == "app/handlers.py" and item["reason"] == "direct repository relationship" for item in results))

    def test_empty_or_generic_query_returns_no_unrelated_context(self):
        self.assertEqual(search_repository("repo", "", 8), [])
        self.assertEqual(search_repository("repo", "and the where", 8), [])


if __name__ == "__main__":
    unittest.main()
