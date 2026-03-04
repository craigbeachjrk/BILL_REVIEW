"""
Lease utility clause integration via the Audit Platform's S3 data.

The Audit Platform (audit.jrkanalytics.com) has every lease page classified by Gemini.
Page types include UTILITY_ADDENDUM. Utility terms are extracted (RUBS allocation,
flat rates, caps, billing company).

S3 bucket: jrk-data-feeds-staging
Paths:
  manifests:       lease_audit/v2/manifests/{property_id}/{lease_id}.json
  classifications: lease_audit/v2/classifications/{property_id}/{page_key}.json
  extractions:     lease_audit/v2/extractions/{property_id}/{page_key}.json
  pages:           lease_audit/v2/pages/{property_id}/{lease_id}/{doc_id}/page_NNN.pdf
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

AUDIT_BUCKET = 'jrk-data-feeds-staging'
LEASE_AUDIT_PREFIX = 'lease_audit/v2'
PAGE_URL_EXPIRY = 604800  # 7 days


@dataclass
class UtilityPage:
    """A lease page classified as a utility addendum."""
    page_key: str
    page_number: int
    doc_id: str
    confidence: float = 0.0
    pdf_s3_key: Optional[str] = None


@dataclass
class UtilityExtraction:
    """AI-extracted utility billing terms from a lease page."""
    page_key: str
    billing_method: Optional[str] = None    # 'RUBS', 'FLAT_RATE', 'SUBMETER', etc.
    rubs_type: Optional[str] = None         # 'OCCUPANCY', 'SQUARE_FOOTAGE', etc.
    utility_types: List[str] = field(default_factory=list)  # ['ELECTRIC', 'GAS', 'WATER', ...]
    monthly_cap: Optional[float] = None
    admin_fee: Optional[float] = None
    billing_company: Optional[str] = None
    effective_date: Optional[str] = None
    raw_text: Optional[str] = None
    raw_extraction: Optional[Dict] = None


class LeaseClauseFinder:
    """
    Retrieves lease utility addendum pages and extracted terms from the Audit Platform's S3 data.

    Usage:
        finder = LeaseClauseFinder(s3_client)
        pages = finder.get_utility_pages('01CHA', '12345')
        if pages:
            extraction = finder.get_utility_extraction('01CHA', pages[0].page_key)
            pdf_url = finder.get_page_pdf_url('01CHA', pages[0].pdf_s3_key)
    """

    def __init__(self, s3_client):
        self.s3 = s3_client
        self.bucket = AUDIT_BUCKET

    def _read_json(self, key: str) -> Optional[Dict]:
        """Read and parse a JSON file from S3."""
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            return json.loads(resp['Body'].read().decode('utf-8'))
        except self.s3.exceptions.NoSuchKey:
            logger.debug(f"S3 key not found: {key}")
            return None
        except Exception as e:
            logger.debug(f"Error reading {key}: {e}")
            return None

    def get_utility_pages(self, property_id: str, lease_id: str) -> List[UtilityPage]:
        """
        Find lease pages classified as UTILITY_ADDENDUM for a specific lease.

        Args:
            property_id: Entity/property ID (e.g. '01CHA')
            lease_id: Entrata lease ID

        Returns:
            List of UtilityPage objects, empty if lease not found or no utility pages.
        """
        # Read lease manifest
        manifest_key = f"{LEASE_AUDIT_PREFIX}/manifests/{property_id}/{lease_id}.json"
        manifest = self._read_json(manifest_key)
        if not manifest:
            logger.debug(f"No manifest for {property_id}/{lease_id}")
            return []

        pages = manifest.get('pages', [])
        utility_pages = []

        for page in pages:
            page_key = page.get('page_key', '')
            if not page_key:
                continue

            # Read classification for this page
            class_key = f"{LEASE_AUDIT_PREFIX}/classifications/{property_id}/{page_key}.json"
            classification = self._read_json(class_key)
            if not classification:
                continue

            page_type = classification.get('page_type', '')
            if page_type != 'UTILITY_ADDENDUM':
                continue

            doc_id = page.get('doc_id', '')
            page_num = page.get('page_number', 0)
            confidence = classification.get('confidence', 0.0)

            # Build the PDF S3 key for this page
            pdf_s3_key = f"{LEASE_AUDIT_PREFIX}/pages/{property_id}/{lease_id}/{doc_id}/page_{page_num:03d}.pdf"

            utility_pages.append(UtilityPage(
                page_key=page_key,
                page_number=page_num,
                doc_id=doc_id,
                confidence=confidence,
                pdf_s3_key=pdf_s3_key,
            ))

        logger.debug(f"Found {len(utility_pages)} utility pages for {property_id}/{lease_id}")
        return utility_pages

    def get_utility_extraction(self, property_id: str, page_key: str) -> Optional[UtilityExtraction]:
        """
        Get AI-extracted utility billing terms for a specific page.

        Args:
            property_id: Entity/property ID
            page_key: Page key from UtilityPage

        Returns:
            UtilityExtraction with structured terms, or None if not available.
        """
        ext_key = f"{LEASE_AUDIT_PREFIX}/extractions/{property_id}/{page_key}.json"
        data = self._read_json(ext_key)
        if not data:
            return None

        utility_terms = data.get('utility_terms', data)

        return UtilityExtraction(
            page_key=page_key,
            billing_method=utility_terms.get('billing_method'),
            rubs_type=utility_terms.get('rubs_type'),
            utility_types=utility_terms.get('utility_types', []),
            monthly_cap=utility_terms.get('monthly_cap'),
            admin_fee=utility_terms.get('admin_fee'),
            billing_company=utility_terms.get('billing_company'),
            effective_date=utility_terms.get('effective_date'),
            raw_text=utility_terms.get('raw_text'),
            raw_extraction=data,
        )

    def get_page_pdf_url(self, s3_key: str, expires: int = PAGE_URL_EXPIRY) -> Optional[str]:
        """
        Generate a presigned URL for a lease page PDF.

        Args:
            s3_key: Full S3 key for the page PDF
            expires: URL expiration in seconds (default 7 days)

        Returns:
            Presigned URL string or None.
        """
        try:
            return self.s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': s3_key},
                ExpiresIn=expires,
            )
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {s3_key}: {e}")
            return None

    def get_lease_utility_info(
        self, property_id: str, lease_id: str
    ) -> Dict:
        """
        Convenience method: get all utility clause info for a lease in one call.

        Returns dict with:
            - pages: list of UtilityPage
            - extractions: list of UtilityExtraction (one per page)
            - page_urls: list of presigned URLs to page PDFs
            - found: bool
        """
        pages = self.get_utility_pages(property_id, lease_id)
        if not pages:
            return {'pages': [], 'extractions': [], 'page_urls': [], 'found': False}

        extractions = []
        page_urls = []
        for page in pages:
            ext = self.get_utility_extraction(property_id, page.page_key)
            if ext:
                extractions.append(ext)
            if page.pdf_s3_key:
                url = self.get_page_pdf_url(page.pdf_s3_key)
                page_urls.append(url)
            else:
                page_urls.append(None)

        return {
            'pages': pages,
            'extractions': extractions,
            'page_urls': page_urls,
            'found': True,
        }
