"""PubMed E-utilities client: search + fetch abstracts as Abstract-format TXT."""

from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

import config

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# 大于该数目时，esearch / efetch 均分段请求后合并
SEGMENT_SIZE = 100
# 硬上限
PUBMED_HARD_CAP = 1500


class PubMedClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "pumed_local/1.0 (local research tool)"}
        )

    def _common_params(self) -> dict[str, str]:
        params: dict[str, str] = {"db": "pubmed", "retmode": "json"}
        if config.NCBI_API_KEY:
            params["api_key"] = config.NCBI_API_KEY
        if config.NCBI_EMAIL:
            params["email"] = config.NCBI_EMAIL
        return params

    def _throttle(self) -> None:
        # 有 API key 约 10 req/s，无 key 约 3 req/s
        time.sleep(0.12 if config.NCBI_API_KEY else 0.35)

    def _clamp_retmax(self, max_results: int | None) -> int:
        retmax = max_results if max_results is not None else config.PUBMED_MAX_RESULTS
        try:
            retmax = int(retmax)
        except (TypeError, ValueError):
            retmax = config.PUBMED_MAX_RESULTS
        return max(1, min(retmax, PUBMED_HARD_CAP))

    def search(self, query: str, max_results: int | None = None) -> list[str]:
        """Return list of PMIDs；超过 SEGMENT_SIZE 时分段 esearch 后合并。"""
        retmax = self._clamp_retmax(max_results)
        if retmax <= SEGMENT_SIZE:
            return self._esearch_page(query, retstart=0, count=retmax)

        pmids: list[str] = []
        retstart = 0
        while len(pmids) < retmax:
            page_size = min(SEGMENT_SIZE, retmax - len(pmids))
            page_ids = self._esearch_page(query, retstart=retstart, count=page_size)
            if not page_ids:
                break
            pmids.extend(page_ids)
            retstart += len(page_ids)
            if len(page_ids) < page_size:
                break
        return pmids[:retmax]

    def _esearch_page(self, query: str, *, retstart: int, count: int) -> list[str]:
        params = self._common_params()
        params.update(
            {
                "term": query,
                "retstart": str(retstart),
                "retmax": str(count),
                "sort": "relevance",
            }
        )
        self._throttle()
        resp = self.session.get(f"{EUTILS_BASE}/esearch.fcgi", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("esearchresult", {})
        return list(result.get("idlist") or [])

    def fetch_abstract_xml(self, pmids: list[str]) -> str:
        """Fetch abstracts；超过 SEGMENT_SIZE 时分段 POST 下载后合并。"""
        if not pmids:
            return ""

        xml_chunks: list[str] = []
        for i in range(0, len(pmids), SEGMENT_SIZE):
            batch = pmids[i : i + SEGMENT_SIZE]
            params = self._common_params()
            params.pop("retmode", None)
            data = {
                **params,
                "id": ",".join(batch),
                "rettype": "abstract",
                "retmode": "xml",
            }
            self._throttle()
            resp = self.session.post(
                f"{EUTILS_BASE}/efetch.fcgi",
                data=data,
                timeout=180,
            )
            resp.raise_for_status()
            xml_chunks.append(resp.text)

        if len(xml_chunks) == 1:
            return xml_chunks[0]
        return self._merge_pubmed_xml(xml_chunks)

    def _merge_pubmed_xml(self, xml_chunks: list[str]) -> str:
        """Merge multiple PubmedArticleSet XML responses into one."""
        articles: list[ET.Element] = []
        for chunk in xml_chunks:
            if not chunk.strip():
                continue
            root = ET.fromstring(chunk)
            articles.extend(list(root.findall("PubmedArticle")))
            articles.extend(list(root.findall("PubmedBookArticle")))

        merged = ET.Element("PubmedArticleSet")
        for article in articles:
            merged.append(article)
        return ET.tostring(merged, encoding="unicode")

    def xml_to_abstract_text(self, xml_text: str) -> str:
        """Convert PubMed XML into a readable Abstract-style plain text."""
        if not xml_text.strip():
            return ""
        root = ET.fromstring(xml_text)
        blocks: list[str] = []

        for article in root.findall(".//PubmedArticle"):
            medline = article.find("MedlineCitation")
            if medline is None:
                continue
            pmid_el = medline.find("PMID")
            pmid = (pmid_el.text or "").strip() if pmid_el is not None else "N/A"

            article_node = medline.find("Article")
            if article_node is None:
                continue

            title_el = article_node.find("ArticleTitle")
            title = "".join(title_el.itertext()).strip() if title_el is not None else ""

            journal_full = ""
            journal_iso = ""
            journal_node = article_node.find("Journal")
            if journal_node is not None:
                title_j = journal_node.find("Title")
                if title_j is not None and title_j.text:
                    journal_full = title_j.text.strip()
                iso = journal_node.find("ISOAbbreviation")
                if iso is not None and iso.text:
                    journal_iso = iso.text.strip()

            pub_date_str = ""
            journal_issue = (
                journal_node.find("JournalIssue") if journal_node is not None else None
            )
            if journal_issue is not None:
                pub_date = journal_issue.find("PubDate")
                if pub_date is not None:
                    y = (pub_date.findtext("Year") or "").strip()
                    month = (pub_date.findtext("Month") or "").strip()
                    day = (pub_date.findtext("Day") or "").strip()
                    medline_date = (pub_date.findtext("MedlineDate") or "").strip()
                    if y:
                        parts = [y]
                        if month:
                            parts.append(month)
                        if day:
                            parts.append(day)
                        pub_date_str = "-".join(parts)
                    elif medline_date:
                        pub_date_str = medline_date

            authors: list[str] = []
            author_list = article_node.find("AuthorList")
            if author_list is not None:
                for author in author_list.findall("Author"):
                    last = author.findtext("LastName") or ""
                    initials = author.findtext("Initials") or ""
                    collective = author.findtext("CollectiveName")
                    if collective:
                        authors.append(collective.strip())
                    elif last:
                        authors.append(f"{last} {initials}".strip())

            abstract_parts: list[str] = []
            abstract_node = article_node.find("Abstract")
            if abstract_node is not None:
                for abstr in abstract_node.findall("AbstractText"):
                    label = abstr.get("Label") or abstr.get("NlmCategory")
                    text = "".join(abstr.itertext()).strip()
                    if not text:
                        continue
                    if label:
                        abstract_parts.append(f"{label}: {text}")
                    else:
                        abstract_parts.append(text)

            abstract = "\n".join(abstract_parts) if abstract_parts else "[No abstract available]"
            journal_display = journal_full or journal_iso or "未提供"

            lines = [
                f"PMID- {pmid}",
                f"TI  - {title}",
                f"JT  - {journal_display}",
                f"TA  - {journal_iso}" if journal_iso else "TA  -",
                f"DP  - {pub_date_str}" if pub_date_str else "DP  - 未提供",
                f"AU  - {'; '.join(authors)}" if authors else "AU  -",
                "AB  -",
                abstract,
                "",
                "-" * 72,
                "",
            ]
            blocks.append("\n".join(lines))

        return "\n".join(blocks).strip() + ("\n" if blocks else "")

    def search_and_save(
        self,
        query: str,
        max_results: int | None = None,
        output_dir: Path | None = None,
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search PubMed and save abstracts into a new project (or output_dir)."""
        kw_list = [str(k).strip() for k in (keywords or []) if str(k).strip()][:2]
        date_stamp = datetime.now().strftime("%Y%m%d")
        pmids = self.search(query, max_results=max_results)
        abstract_text = ""
        if pmids:
            xml_text = self.fetch_abstract_xml(pmids)
            abstract_text = self.xml_to_abstract_text(xml_text)

        project_dir: Path | None = None
        pdf_dir: Path | None = None
        if output_dir is None:
            project_dir, filename = self._allocate_project(kw_list, date_stamp)
            out_dir = project_dir
            pdf_dir = project_dir / "_full_pdf"
        else:
            out_dir = output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            filename = self._make_filename(kw_list, date_stamp, out_dir)

        filepath = out_dir / filename
        kw_header = ", ".join(kw_list) if kw_list else ""
        project_name = project_dir.name if project_dir is not None else ""
        header = (
            f"PubMed Abstract Export\n"
            f"Project: {project_name}\n"
            f"Keywords: {kw_header}\n"
            f"Query: {query}\n"
            f"Retrieved: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Count: {len(pmids)}\n"
            f"PMIDs: {', '.join(pmids)}\n"
            f"{'=' * 72}\n\n"
        )
        filepath.write_text(header + abstract_text, encoding="utf-8")

        return {
            "pmids": pmids,
            "count": len(pmids),
            "filepath": str(filepath),
            "filename": filename,
            "text": abstract_text,
            "keywords": kw_list,
            "project_dir": str(project_dir) if project_dir is not None else "",
            "project_name": project_name,
            "pdf_dir": str(pdf_dir) if pdf_dir is not None else "",
        }

    def _allocate_project(self, keywords: list[str], date_stamp: str) -> tuple[Path, str]:
        """Copy `_template` into a new project folder. Return (dir, abstract filename)."""
        root = config.PROJECTS_DIR
        root.mkdir(parents=True, exist_ok=True)
        parts = [self._sanitize_token(k) for k in keywords]
        parts = [p for p in parts if p][:2] or ["pubmed"]
        base = "_".join(parts)

        def names(suffix: str) -> tuple[str, str]:
            # 与 _template/_template_abstract_date.txt 对应：{关键词}_abstract_{日期}.txt
            folder = f"{base}_{date_stamp}{suffix}"
            abstract = f"{base}_abstract_{date_stamp}{suffix}.txt"
            return folder, abstract

        folder, abstract = names("")
        n = 2
        while (root / folder).exists():
            folder, abstract = names(f"_{n}")
            n += 1

        project = root / folder
        project.mkdir(parents=True, exist_ok=False)
        template_pdf = root / "_template" / "_full_pdf"
        dest_pdf = project / "_full_pdf"
        if template_pdf.is_dir():
            shutil.copytree(template_pdf, dest_pdf)
        else:
            dest_pdf.mkdir(parents=True, exist_ok=True)
        return project, abstract

    @staticmethod
    def _sanitize_token(token: str, max_len: int = 24) -> str:
        """Make a filesystem-safe keyword token for Windows filenames."""
        bad = '<>:"/\\|?*\n\r\t'
        cleaned = "".join("_" if ch in bad else ch for ch in token.strip())
        cleaned = cleaned.replace(" ", "")
        cleaned = cleaned.strip("._")
        if not cleaned:
            return ""
        return cleaned[:max_len]

    def _make_filename(
        self, keywords: list[str], date_stamp: str, out_dir: Path
    ) -> str:
        parts = [self._sanitize_token(k) for k in keywords]
        parts = [p for p in parts if p][:2]
        if not parts:
            parts = ["pubmed"]
        base = "_".join(parts) + f"_{date_stamp}"
        candidate = f"{base}.txt"
        if not (out_dir / candidate).exists():
            return candidate
        n = 2
        while True:
            candidate = f"{base}_{n}.txt"
            if not (out_dir / candidate).exists():
                return candidate
            n += 1
