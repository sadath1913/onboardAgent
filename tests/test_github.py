import unittest

from backend.github import RepositoryInputError, validate_github_url


class GitHubUrlTests(unittest.TestCase):
    def test_normalizes_public_repository_url(self):
        repo = validate_github_url("https://github.com/octo/widgets")
        self.assertEqual(repo.owner, "octo")
        self.assertEqual(repo.name, "widgets")
        self.assertEqual(repo.url, "https://github.com/octo/widgets.git")

    def test_accepts_git_suffix(self):
        repo = validate_github_url("https://github.com/octo/widgets.git/")
        self.assertEqual(repo.name, "widgets")

    def test_rejects_non_github_hosts_and_non_https_urls(self):
        for value in ("http://github.com/octo/widgets", "https://github.com.evil.test/octo/widgets", "https://gitlab.com/octo/widgets"):
            with self.subTest(value=value), self.assertRaises(RepositoryInputError):
                validate_github_url(value)

    def test_rejects_credentials_extra_paths_and_query_strings(self):
        values = (
            "https://someone@github.com/octo/widgets",
            "https://github.com/octo/widgets/tree/main",
            "https://github.com/octo/widgets?token=secret",
            "https://github.com/../widgets",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(RepositoryInputError):
                validate_github_url(value)


if __name__ == "__main__":
    unittest.main()
