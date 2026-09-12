import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def tag(element):
    return element.tag.rsplit('}',1)[-1]


def normalized_name(value):
    return re.sub(r'\s+',' ',value).strip().casefold()


def import_xml(db,run_id,source,snapshot_id,path):
    content = path.read_bytes()
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('XML DTD/entities are not accepted')
    root = ET.fromstring(content)
    if source['id']=='fid_un':
        return import_un(db,run_id,source,snapshot_id,root)
    is_eu = source['id']=='fid_eu'
    if tag(root) != ('export' if is_eu else 'LVlist'):
        raise ValueError('Unexpected sanctions XML schema')
    generation = root.get('generationDate') if is_eu else root.findtext('./PublishInfo/PublishDate')
    count = 0
    for entity in root:
        if tag(entity) != ('sanctionEntity' if is_eu else 'Entity'):
            continue
        entity_id = entity.get('logicalId') if is_eu else entity.findtext('Id')
        if not entity_id:
            raise ValueError('Sanction entity has no ID')
        children = list(entity)
        subject = next((c for c in children if tag(c)=='subjectType'),None)
        regulation = next((c for c in children if tag(c)=='regulation'),None)
        entity_type = subject.get('code') if subject is not None else entity.findtext('Type')
        program = regulation.get('programme') if regulation is not None else entity.findtext('Program')
        legal_url = next((c.text for c in regulation if tag(c)=='publicationUrl'),None) if regulation is not None else entity.findtext('Link')
        names = [c.get('wholeName') for c in children if tag(c)=='nameAlias'] if is_eu else [c.text for c in entity.iter() if tag(c) in {'WholeName','AliasWholeName'}]
        names = sorted({n.strip() for n in names if n and n.strip()})
        if not names:
            raise ValueError('Sanction entity has no usable name')
        db.execute('INSERT INTO sanction_entities VALUES (?,?,?,?,?,?,?,?)',
                   (run_id,source['id'],entity_id,entity_type,program,legal_url,ET.tostring(entity,encoding='unicode'),snapshot_id))
        for name in names:
            db.execute('INSERT INTO sanction_names VALUES (?,?,?,?,?)', (run_id,source['id'],entity_id,name,normalized_name(name)))
        for index,child in enumerate(children):
            values = {'attributes':child.attrib,'text':child.text.strip() if child.text else None,
                      'children':[{'tag':tag(c),'attributes':c.attrib,'text':c.text} for c in child]}
            db.execute('INSERT INTO sanction_attributes VALUES (?,?,?,?,?,?)',
                       (run_id,source['id'],entity_id,index,tag(child),json.dumps(values,ensure_ascii=False)))
            if tag(child) in {'identification','Document'}:
                db.execute('INSERT INTO sanction_identifiers VALUES (?,?,?,?,?,?,?)',
                           (run_id,source['id'],entity_id,index,child.get('identificationTypeCode') or child.findtext('DocumentType'),
                            child.get('number') or child.findtext('DocumentNumber'),child.get('countryIso2Code') or child.findtext('DocumentCountryIso2Code')))
        count += 1
    if not count:
        raise ValueError('Sanctions XML has no entities')
    return count,generation,date_warning(generation)


def date_warning(generation):
    warning = 'SOURCE_DATE_MISSING'
    if generation:
        published = datetime.fromisoformat(generation.replace('Z','+00:00'))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        warning = None
        if published > datetime.now(timezone.utc):
            warning = 'SOURCE_DATE_IN_FUTURE'
        elif (datetime.now(timezone.utc)-published).total_seconds()>7*86400:
            warning = 'SOURCE_DATE_OLDER_THAN_7_DAYS; date does not prove a newer list exists'
    return warning


def import_un(db,run_id,source,snapshot_id,root):
    if tag(root)!='CONSOLIDATED_LIST':
        raise ValueError('Unexpected UN sanctions XML schema')
    count=0
    for group,kind in [('INDIVIDUALS','person'),('ENTITIES','enterprise')]:
        container=root.find(group)
        if container is None:
            raise ValueError('UN list section missing')
        for entity in container:
            entity_id=entity.findtext('DATAID')
            name=' '.join(entity.findtext(k,'').strip() for k in ['FIRST_NAME','SECOND_NAME','THIRD_NAME','FOURTH_NAME']).strip()
            if not entity_id or not name:
                raise ValueError('UN entity ID/name missing')
            names={name}
            names.update(e.text.strip() for e in entity.iter('ALIAS_NAME') if e.text and e.text.strip())
            names.update(e.text.strip() for e in entity.iter('NAME_ORIGINAL_SCRIPT') if e.text and e.text.strip())
            db.execute('INSERT INTO sanction_entities VALUES (?,?,?,?,?,?,?,?)',
                (run_id,source['id'],entity_id,kind,entity.findtext('UN_LIST_TYPE'),
                 'https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list',ET.tostring(entity,encoding='unicode'),snapshot_id))
            for name in sorted(names):
                db.execute('INSERT INTO sanction_names VALUES (?,?,?,?,?)',(run_id,source['id'],entity_id,name,normalized_name(name)))
            for index,child in enumerate(entity):
                db.execute('INSERT INTO sanction_attributes VALUES (?,?,?,?,?,?)',
                    (run_id,source['id'],entity_id,index,tag(child),json.dumps({'xml':ET.tostring(child,encoding='unicode')},ensure_ascii=False)))
                if tag(child)=='INDIVIDUAL_DOCUMENT' and child.findtext('NUMBER'):
                    db.execute('INSERT INTO sanction_identifiers VALUES (?,?,?,?,?,?,?)',
                        (run_id,source['id'],entity_id,index,child.findtext('TYPE_OF_DOCUMENT'),child.findtext('NUMBER'),child.findtext('COUNTRY_OF_ISSUE')))
            count+=1
    if not count: raise ValueError('UN sanctions XML has no entities')
    generated=root.get('dateGenerated')
    return count,generated,date_warning(generated)
