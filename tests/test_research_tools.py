import unittest
from unittest import mock

import plugins.research_tools as research
import plugins.tax_tools as tax

PAGE = """<html><head><title>x</title><script>var a=1;</script></head><body>
<h1>Dedução PPR</h1><p>Pode deduzir 20% do valor aplicado.</p><p>Outro assunto.</p></body></html>"""


class FetchOfficialPageTest(unittest.TestCase):
    def setUp(self):
        tax.configure({"jurisdiction": "pt"})

    def fetch(self, url, **kwargs):
        return research.fetch_official_page.func(url, **kwargs)

    def test_only_official_https_sources(self):
        self.assertEqual(self.fetch("http://info.portaldasfinancas.gov.pt/x")["status"], "REFUSED")
        self.assertEqual(self.fetch("https://some-blog.example.com/irs")["status"], "REFUSED")

    def test_allowed_page_is_labelled_unverified_and_cleaned(self):
        with mock.patch.object(research, "_download", return_value=PAGE):
            result = self.fetch("https://info.portaldasfinancas.gov.pt/pt/ppr")
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertIn("Pode deduzir 20%", result["text"])
        self.assertNotIn("var a", result["text"])
        self.assertIn("source", result)

    def test_keyword_filter(self):
        with mock.patch.object(research, "_download", return_value=PAGE):
            result = self.fetch("https://www.seg-social.pt/x", look_for="deduzir")
        self.assertEqual(result["text"], "Pode deduzir 20% do valor aplicado.")

    def test_government_domains_allowed_for_other_countries(self):
        tax.configure({"jurisdiction": "generic"})
        self.addCleanup(tax.configure, {"jurisdiction": "pt"})
        with mock.patch.object(research, "_download", return_value="<p>ok</p>"):
            self.assertEqual(self.fetch("https://www.agenciatributaria.gob.es/x")["status"], "UNVERIFIED")

    def test_network_failure_is_reported(self):
        with mock.patch.object(research, "_download", side_effect=OSError("offline")):
            self.assertEqual(self.fetch("https://www.bportugal.pt/x")["status"], "FAILED")

    def test_facts_lookup_points_to_official_sources_when_not_covered(self):
        tax.configure({"jurisdiction": "generic"})
        self.addCleanup(tax.configure, {"jurisdiction": "pt"})
        result = tax.jurisdiction_facts.func("banking")
        self.assertEqual(result["not_covered"], "banking")
        self.assertIn("fetch_official_page", result["if_not_covered"])


if __name__ == "__main__":
    unittest.main()
