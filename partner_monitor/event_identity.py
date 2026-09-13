"""Conservative evidence-based event identities; no model-inferred grouping."""
import re

def identity(finding, reviewed_case=None):
    if reviewed_case:
        return ['case',reviewed_case]
    quote=finding['evidence_quote']
    # Latvian civil-case identifier. Require an explicit case label in the quotation;
    # summaries, URLs, dates and company registration numbers are not evidence of a case ID.
    cases=set(re.findall(r'(?i)(?:case\s+(?:no\.?|number)|liet(?:a|as|ā)\s*(?:nr\.?)?)\s*[:#]?\s*(C[0-9]{8,9})(?![0-9A-Za-z])',quote))
    if len(cases)==1:
        return ['latvian_civil_case',next(iter(cases)).upper()]
    return ['quote',' '.join(quote.split())]
