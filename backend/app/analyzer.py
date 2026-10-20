"""Static source and OpenAPI contract analyzer. Uploaded source is never executed or extracted to disk."""
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 80 * 1024 * 1024
MAX_ENTRIES = 2500
MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED = {'.py', '.js', '.jsx', '.ts', '.tsx', '.dart', '.kt', '.java', '.json', '.yaml', '.yml', '.go', '.php', '.rb', '.cs'}
SKIP = {'node_modules', '.git', '.venv', 'venv', 'build', 'dist', 'coverage', 'vendor', 'Pods', '__pycache__'}
HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace'}


def read_archive(raw: bytes):
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError('ZIP exceeds 20 MB. Upload a smaller source archive.')
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError('Upload a valid .zip source-code archive.') from exc
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ValueError('ZIP contains more than 2500 entries.')
    total = sum(i.file_size for i in infos if not i.is_dir())
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ValueError('ZIP expands beyond the 80 MB safety limit.')
    files, warnings = [], []
    for info in infos:
        path = PurePosixPath(info.filename)
        # Reject path traversal and Unix symlink entries; content stays in memory.
        is_symlink = ((info.external_attr >> 16) & 0o170000) == 0o120000
        if is_symlink:
            warnings.append('Skipped symbolic link entry.')
            continue
        if path.is_absolute() or '..' in path.parts:
            warnings.append('Skipped unsafe archive path.')
            continue
        if info.is_dir() or any(part in SKIP for part in path.parts) or path.suffix.lower() not in ALLOWED:
            continue
        if info.file_size > MAX_FILE_BYTES:
            warnings.append('Skipped large file: ' + info.filename)
            continue
        try:
            content = zf.read(info)
            if b'\x00' not in content[:4096]:
                files.append((info.filename, content.decode('utf-8', errors='ignore')))
        except Exception:
            warnings.append('Could not read file: ' + info.filename)
    return files, warnings


def _scan_source(files, role):
    fields, routes, matched = set(), set(), []
    extensions = {PurePosixPath(name).suffix.lower() for name, _ in files}
    platform = 'flutter' if '.dart' in extensions else 'android_native' if extensions & {'.kt', '.java'} else 'web' if extensions & {'.tsx', '.ts', '.jsx', '.js'} else 'backend_or_unknown'
    api_calls, mobile_frameworks = set(), set()
    route_patterns = [
        r"@(?:app|router)\.(?:get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)",
        r"(?:app|router)\.(?:get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)",
        r"(?:path|route)\s*[:=]\s*['\"]([^'\"]+)",
        r"@RequestMapping\s*\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)",
    ]
    field_patterns = [
        r'\b(?:response|data|result|profile|user|body|payload)\.([A-Za-z_][A-Za-z0-9_]*)',
        r"\b(?:response|data|result|profile|user|body|payload)\s*\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]",
        r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*:\s*(?:str|int|float|bool|Optional|List|Dict|String|Number|Boolean|\{|\[)",
        r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\??\s*:\s*(?:string|number|boolean)\b',
        r'\b(?:final|late|var)\s+(?:String|int|double|bool)\s+([A-Za-z_][A-Za-z0-9_]*)',
    ]
    api_patterns = [r'@(?:GET|POST|PUT|PATCH|DELETE)\s*\(\s*[\"\']([^\"\']+)', r'(?:Dio|http|client|apiClient)\.(?:get|post|put|patch|delete)\s*\(\s*[\"\']([^\"\']+)', r'baseUrl\s*[:=]\s*[\"\']([^\"\']+)', r'@(?:SerializedName|Json|JsonProperty|SerialName)\s*\(\s*[\"\']([^\"\']+)']
    for name, source in files:
        found = False
        lowname = name.lower()
        if lowname.endswith('.dart') and any(x in source for x in ('package:dio/', 'package:http/')): mobile_frameworks.add('Dart HTTP/Dio')
        if lowname.endswith(('.kt', '.java')) and any(x in source for x in ('retrofit2.http', 'okhttp3.', 'retrofit2.Retrofit')): mobile_frameworks.add('Android Retrofit/OkHttp')
        if lowname.endswith(('.kt', '.java')) and 'androidx.compose' in source: mobile_frameworks.add('Android Jetpack Compose')
        for pat in route_patterns:
            for value in re.findall(pat, source, re.I):
                if value.startswith('/') and len(value) < 160:
                    routes.add(value); found = True
        for pat in api_patterns:
            for value in re.findall(pat, source, re.I):
                if value.startswith('/') or value.startswith('http'):
                    api_calls.add(value[:180]); found = True
        for pat in field_patterns:
            for value in re.findall(pat, source):
                if value.lower() not in {'true','false','null','none','string','number','object','array'}:
                    fields.add(value); found = True
        if found:
            matched.append(name)
    noise = {'id','key','type','name','value','data','result','response','body','payload','json','status','message','error','code','token','url','path','method','headers','params','query','request','model','class','return','self','app','router'}
    return {'role': role, 'platform_detected': platform, 'source_extensions': sorted(extensions), 'mobile_frameworks_detected': sorted(mobile_frameworks), 'api_calls_or_serialized_names': sorted(api_calls)[:300], 'files_scanned': len(files), 'files_with_matches': matched[:100], 'routes': sorted(routes), 'fields': sorted(f for f in fields if f.lower() not in noise)[:500]}


def _load_openapi_documents(files):
    docs = []
    for name, source in files:
        lower = name.lower()
        if not (lower.endswith(('.json', '.yaml', '.yml')) and ('openapi' in lower or 'swagger' in lower or 'api-spec' in lower or 'api_spec' in lower or 'openapi:' in source[:5000] or '"openapi"' in source[:2000] or '"swagger"' in source[:2000])):
            continue
        try:
            if lower.endswith('.json'):
                doc = json.loads(source)
            else:
                try:
                    import yaml
                    doc = yaml.safe_load(source)
                except ImportError:
                    continue
            if isinstance(doc, dict) and isinstance(doc.get('paths'), dict) and ('openapi' in doc or 'swagger' in doc):
                docs.append((name, doc))
        except Exception:
            continue
    return docs


def _resolve_schema(schema: Any, components: dict, depth=0):
    """Resolve a useful subset of OpenAPI schema metadata without executing anything."""
    if not isinstance(schema, dict) or depth > 12:
        return {
            'properties': {}, 'required': set(), 'type': schema.get('type') if isinstance(schema, dict) else None,
            'format': None, 'enum': None, 'nullable': None, 'items': None,
        }
    ref = schema.get('$ref')
    resolved = {'properties': {}, 'required': set(), 'type': None, 'format': None, 'enum': None, 'nullable': None, 'items': None}
    if isinstance(ref, str) and (ref.startswith('#/components/schemas/') or ref.startswith('#/definitions/')):
        key = ref.rsplit('/', 1)[-1].replace('~1', '/').replace('~0', '~')
        target = components.get(key, {})
        resolved = _resolve_schema(target, components, depth + 1)
    properties = dict(resolved.get('properties') or {})
    properties.update(schema.get('properties') or {})
    required = set(resolved.get('required') or set()) | set(schema.get('required') or [])
    for composition in ('allOf', 'oneOf', 'anyOf'):
        children = schema.get(composition, [])
        for child in children if isinstance(children, list) else []:
            part = _resolve_schema(child, components, depth + 1)
            properties.update(part['properties'])
            required |= part['required']
    item_schema = schema.get('items')
    if item_schema is not None:
        item_info = _resolve_schema(item_schema, components, depth + 1)
        items = {'type': item_info.get('type'), 'format': item_info.get('format'), 'enum': item_info.get('enum')}
    else:
        items = resolved.get('items')
    return {
        'properties': properties,
        'required': required,
        'type': schema.get('type', resolved.get('type')),
        'format': schema.get('format', resolved.get('format')),
        'enum': schema.get('enum', resolved.get('enum')),
        'nullable': schema.get('nullable', resolved.get('nullable')),
        'items': items,
    }


def _schema_property_info(prop, components):
    info = _resolve_schema(prop, components)
    return {k: info.get(k) for k in ('type', 'format', 'enum', 'nullable', 'items')}


def _operation_contract(doc):
    # OpenAPI 3 uses components.schemas; Swagger 2 uses definitions.
    components = (doc.get('components') or {}).get('schemas') or doc.get('definitions') or {}
    paths = {}
    global_security = doc.get('security', [])
    for path, path_item in (doc.get('paths') or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            key = (method.upper(), path)
            parameters = {}
            for parameter in list(path_item.get('parameters') or []) + list(op.get('parameters') or []):
                if isinstance(parameter, dict) and parameter.get('name') and parameter.get('in'):
                    if parameter.get('in') == 'body':
                        continue
                    ps = _resolve_schema(parameter.get('schema') or {}, components)
                    pkey = (parameter.get('in'), parameter.get('name'))
                    parameters[pkey] = {
                        'required': bool(parameter.get('required', False)),
                        'type': ps.get('type') or parameter.get('type'), 'format': ps.get('format') or parameter.get('format'),
                    }
            request_props, request_required = {}, set()
            request_body = op.get('requestBody') or {}
            request_content = request_body.get('content') or {}
            request_media_types = set(request_content)
            body_required = bool(request_body.get('required', False))
            for media in request_content.values():
                schema = _resolve_schema(media.get('schema', {}), components)
                request_props.update(schema['properties']); request_required |= schema['required']
            # Swagger 2 describes request bodies as parameters with in=body.
            for p in list(path_item.get('parameters') or []) + list(op.get('parameters') or []):
                if isinstance(p, dict) and p.get('in') == 'body':
                    schema = _resolve_schema(p.get('schema') or {}, components)
                    request_props.update(schema['properties']); request_required |= schema['required']
                    body_required = body_required or bool(p.get('required', False))
            if not request_media_types and doc.get('swagger') == '2.0':
                request_media_types = set(op.get('consumes') or path_item.get('consumes') or doc.get('consumes') or [])
            response_map = {}
            for status, response in (op.get('responses') or {}).items():
                response_props, response_required = {}, set()
                response_media_types = set()
                if isinstance(response, dict):
                    response_content = response.get('content') or {}
                    response_media_types = set(response_content)
                    for media in response_content.values():
                        schema = _resolve_schema(media.get('schema', {}), components)
                        response_props.update(schema['properties']); response_required |= schema['required']
                    # Swagger 2 stores response schema directly under response.schema.
                    if not response_content and isinstance(response.get('schema'), dict):
                        schema = _resolve_schema(response['schema'], components)
                        response_props.update(schema['properties']); response_required |= schema['required']
                        response_media_types = set(op.get('produces') or path_item.get('produces') or doc.get('produces') or [])
                response_map[str(status)] = {
                    'properties': response_props, 'required': response_required,
                    'media_types': response_media_types,
                }
            paths[key] = {
                'operation_id': op.get('operationId'), 'parameters': parameters,
                'request': {'properties': request_props, 'required': request_required,
                            'required_body': body_required, 'media_types': request_media_types},
                'responses': response_map,
                'secured': bool(op.get('security', global_security)),
                'security_declared': 'security' in op or 'security' in doc,
                'deprecated': bool(op.get('deprecated', False)),
            }
    return paths


def _add_finding(findings, severity, category, location, message, evidence, recommendation):
    findings.append({'severity': severity, 'category': category, 'location': location,
                     'message': message, 'evidence': evidence, 'recommendation': recommendation})


def _compare_property(findings, location, name, old_prop, new_prop, direction, old_components=None, new_components=None):
    old_components = old_components or {}
    new_components = new_components or {}
    old_info = _schema_property_info(old_prop, old_components) if isinstance(old_prop, dict) else {}
    new_info = _schema_property_info(new_prop, new_components) if isinstance(new_prop, dict) else {}
    old_type, new_type = old_info.get('type'), new_info.get('type')
    if old_type and new_type and old_type != new_type:
        _add_finding(findings, 'high', f'{direction}_type_changed', location,
            f'{direction.replace("_", " ").capitalize()} field "{name}" changed type from {old_type} to {new_type}.',
            f'OpenAPI schema type changed: {old_type} → {new_type}.',
            'Preserve the previous type or publish a versioned contract.')
    old_format, new_format = old_info.get('format'), new_info.get('format')
    if old_format and new_format and old_format != new_format:
        _add_finding(findings, 'medium', f'{direction}_format_changed', location,
            f'Field "{name}" changed format from {old_format} to {new_format}.',
            f'OpenAPI schema format changed: {old_format} → {new_format}.',
            'Confirm old clients accept the new format; keep the previous format or version the contract.')
    if old_info.get('nullable') is True and new_info.get('nullable') is False:
        _add_finding(findings, 'high', f'{direction}_nullability_narrowed', location,
            f'Field "{name}" no longer allows null values.',
            'Old contract declared nullable=true; new contract declares nullable=false.',
            'Keep null support or ensure older clients never send/receive null for this field.')
    old_enum, new_enum = old_info.get('enum'), new_info.get('enum')
    if isinstance(old_enum, list) and isinstance(new_enum, list) and set(new_enum) < set(old_enum):
        removed_values = sorted(set(old_enum) - set(new_enum), key=str)
        _add_finding(findings, 'high', f'{direction}_enum_narrowed', location,
            f'Field "{name}" no longer permits previously allowed values.',
            f'Removed enum values: {removed_values}.',
            'Continue accepting old enum values or provide an explicit compatibility mapping.')
    old_items, new_items = old_info.get('items'), new_info.get('items')
    if old_type == 'array' and new_type == 'array' and old_items and new_items:
        oi, ni = old_items.get('type'), new_items.get('type')
        if oi and ni and oi != ni:
            _add_finding(findings, 'high', f'{direction}_array_item_type_changed', location,
                f'Array field "{name}" changed item type from {oi} to {ni}.',
                f'Array item type changed: {oi} → {ni}.',
                'Preserve the previous item type or version the response/request contract.')


def _compare_openapi(old_docs, new_docs):
    findings = []
    if not old_docs or not new_docs:
        return findings, {'old_spec_found': bool(old_docs), 'new_spec_found': bool(new_docs), 'operations_compared': 0,
                          'analysis_mode': 'source_fallback'}
    old_contract, new_contract = _operation_contract(old_docs[0][1]), _operation_contract(new_docs[0][1])
    for key, old in old_contract.items():
        method, path = key
        prefix = f'{method} {path}'
        new = new_contract.get(key)
        if new is None:
            _add_finding(findings, 'critical', 'route_removed', prefix,
                'Operation present in old OpenAPI contract is missing from new contract.',
                'Old contract contains operation; new contract does not.',
                'Keep the operation, add a versioned route, or provide a backward-compatible adapter.')
            continue
        old_params, new_params = old['parameters'], new['parameters']
        for pkey, old_param in old_params.items():
            loc, name = pkey
            new_param = new_params.get(pkey)
            if new_param is None:
                _add_finding(findings, 'low', 'parameter_removed', prefix,
                    f'Parameter "{name}" was removed from the contract.',
                    f'Old contract declares {loc} parameter {name}.',
                    'Confirm no supported client relies on this parameter.')
                continue
            if not old_param['required'] and new_param['required']:
                _add_finding(findings, 'high', 'required_parameter_added', prefix,
                    f'Existing {loc} parameter "{name}" is now required.',
                    'Old contract marked it optional; new contract marks it required.',
                    'Keep it optional, supply a server-side default, or version the endpoint.')
            if old_param.get('type') and new_param.get('type') and old_param['type'] != new_param['type']:
                _add_finding(findings, 'high', 'parameter_type_changed', prefix,
                    f'Parameter "{name}" changed type from {old_param["type"]} to {new_param["type"]}.',
                    f'OpenAPI parameter type changed: {old_param["type"]} → {new_param["type"]}.',
                    'Preserve the previous type or introduce a versioned parameter.')
        for pkey, new_param in new_params.items():
            if pkey not in old_params and new_param['required']:
                loc, name = pkey
                _add_finding(findings, 'high', 'required_parameter_added', prefix,
                    f'New required {loc} parameter "{name}" may break old clients.',
                    f'New contract requires {loc} parameter {name} ({new_param.get("type") or "unspecified type"}).',
                    'Make it optional, supply a server-side default, or version the endpoint.')
        old_schema, new_schema = old['request'], new['request']
        if not old_schema['required_body'] and new_schema['required_body']:
            _add_finding(findings, 'high', 'request_body_now_required', prefix,
                'Request body changed from optional to required.',
                'Old contract did not require a body; new contract does.',
                'Keep the body optional or provide a backward-compatible default.')
        for media_type in sorted(old_schema['media_types'] - new_schema['media_types']):
            _add_finding(findings, 'medium', 'request_media_type_removed', prefix,
                f'Request content type "{media_type}" is no longer declared.',
                f'Old request accepted {media_type}; new contract does not declare it.',
                'Continue accepting the old content type or update supported clients deliberately.')
        for name in sorted(set(old_schema['properties']) - set(new_schema['properties'])):
            _add_finding(findings, 'high', 'request_field_removed', prefix,
                f'Request field "{name}" was removed.', f'Old request schema contains {name}; new schema does not.',
                'Continue accepting the old field or add a compatibility mapping.')
        for name in sorted(new_schema['required'] - old_schema['required']):
            _add_finding(findings, 'high', 'request_field_now_required', prefix,
                f'Request field "{name}" is now required.',
                'The new contract marks this field required; the old contract did not.',
                'Keep it optional or apply a server-side default for old clients.')
        for name in sorted(set(old_schema['properties']) & set(new_schema['properties'])):
            _compare_property(findings, prefix, name, old_schema['properties'][name], new_schema['properties'][name], 'request', (old_docs[0][1].get('components') or {}).get('schemas', {}), (new_docs[0][1].get('components') or {}).get('schemas', {}))
        for status, old_resp in old['responses'].items():
            new_resp = new['responses'].get(status)
            if new_resp is None:
                _add_finding(findings, 'medium', 'response_status_removed', prefix,
                    f'Response status {status} is no longer declared.', f'Old contract declares response status {status}.',
                    'Check whether old clients depend on this response/status handling.')
                continue
            for media_type in sorted(old_resp['media_types'] - new_resp['media_types']):
                _add_finding(findings, 'medium', 'response_media_type_removed', prefix,
                    f'Response content type "{media_type}" is no longer declared for status {status}.',
                    f'Old contract declared {media_type}; new contract does not.',
                    'Continue returning the old content type or verify all clients negotiate a supported type.')
            for name in sorted(old_resp['required'] - new_resp['required']):
                _add_finding(findings, 'medium', 'response_field_no_longer_required', prefix,
                    f'Response field "{name}" is no longer guaranteed required.',
                    'Old schema required the field; new schema does not.',
                    'Continue returning the field for old clients or version the response.')
            for name in sorted(set(old_resp['properties']) - set(new_resp['properties'])):
                _add_finding(findings, 'high', 'response_field_removed', prefix,
                    f'Response field "{name}" was removed.', f'Old response schema contains {name}; new schema does not.',
                    'Keep the old response field or map it from the new field.')
            for name in sorted(set(old_resp['properties']) & set(new_resp['properties'])):
                _compare_property(findings, prefix, name, old_resp['properties'][name], new_resp['properties'][name], 'response', (old_docs[0][1].get('components') or {}).get('schemas', {}), (new_docs[0][1].get('components') or {}).get('schemas', {}))
        if old['secured'] and not new['secured']:
            _add_finding(findings, 'medium', 'security_requirement_removed', prefix,
                'Security requirement disappeared from the new operation contract.',
                'Old operation required security; new operation does not declare it.',
                'Verify whether this is intentional; review authorization policy separately.')
        if not old['deprecated'] and new['deprecated']:
            _add_finding(findings, 'low', 'operation_deprecated', prefix,
                'Operation is now marked deprecated.', 'New contract declares deprecated=true.',
                'Plan a migration path and communicate the deprecation to client owners.')
    summary = {
        'old_spec_found': True, 'new_spec_found': True,
        'operations_compared': len(set(old_contract) & set(new_contract)),
        'old_operations': len(old_contract), 'new_operations': len(new_contract),
        'old_only_operations': len(set(old_contract) - set(new_contract)),
        'new_only_operations': len(set(new_contract) - set(old_contract)),
        'spec_files': {'old': old_docs[0][0], 'new': new_docs[0][0]},
        'analysis_mode': 'openapi_contract',
    }
    return findings, summary

def analyze_archives(old_frontend: bytes, old_backend: bytes, new_backend: bytes):
    scans, archives, warnings = [], [], []
    for role, raw in [('old_frontend', old_frontend), ('old_backend', old_backend), ('new_backend', new_backend)]:
        files, ws = read_archive(raw)
        warnings.extend([role + ': ' + w for w in ws])
        if not files:
            raise ValueError('No supported source files found in ' + role + ' ZIP. Include source files such as .tsx, .dart, .kt, .js or .py.')
        archives.append(files)
        scans.append(_scan_source(files, role))
    fe, old, new = scans
    removed = sorted(set(old['fields']) - set(new['fields']))
    added = sorted(set(new['fields']) - set(old['fields']))
    routes_removed = sorted(set(old['routes']) - set(new['routes']))
    affected = sorted(set(removed) & set(fe['fields']))
    old_specs = _load_openapi_documents(archives[1])
    new_specs = _load_openapi_documents(archives[2])
    contract_findings, contract_summary = _compare_openapi(old_specs, new_specs)
    findings = list(contract_findings)
    for route in routes_removed:
        findings.append({'severity':'high','category':'source_route_removed','location':route,'message':'Route string detected in old backend was not detected in new backend.','evidence':'Heuristic source scan only; dynamic routes may be missed.','recommendation':'Verify route registration and preserve a compatibility route if old clients use it.'})
    for field in affected:
        findings.append({'severity':'high','category':'frontend_field_overlap','location':field,'message':f'Old frontend appears to use "{field}", which was not detected in new backend fields.','evidence':'Name overlap inferred from source patterns; not proof of a response contract.','recommendation':'Check serializers and API response payloads; keep an alias or map the field if needed.'})
    severity_counts = {s:sum(1 for f in findings if f['severity']==s) for s in ('critical','high','medium','low')}
    risk_score = min(100, severity_counts['critical']*35 + severity_counts['high']*18 + severity_counts['medium']*8 + severity_counts['low']*2)
    risk_level = 'critical' if risk_score >= 70 else 'high' if risk_score >= 35 else 'moderate' if risk_score >= 12 else 'low'
    status = 'potential_breaking_changes' if any(f['severity'] in ('critical','high') for f in findings) or removed or routes_removed else 'no_obvious_breaks_detected'
    if not old_specs or not new_specs:
        warnings.append('OpenAPI contract comparison not run: include an OpenAPI/Swagger JSON or YAML file in both backend ZIPs for deeper contract analysis.')
    return {
        'analysis_type':'mobile_and_backend_static_compatibility_analysis', 'status':status,
        'client_platform': fe.get('platform_detected', 'unknown'),
        'client_mobile_frameworks': fe.get('mobile_frameworks_detected', []),
        'compatibility_matrix': [
            {'client': 'Uploaded legacy client', 'backend': 'Current backend', 'relationship': 'baseline_reference', 'status': 'baseline_not_runtime_tested'},
            {'client': 'Uploaded legacy client', 'backend': 'Proposed backend', 'relationship': 'upgrade_candidate', 'status': 'potential_breaking_changes' if (any(f['severity'] in ('critical','high') for f in findings) or removed or routes_removed) else 'no_obvious_breaks_detected', 'risk_score': risk_score},
        ],
        'analysis_coverage': {
            'source_scan': 'completed',
            'openapi_contract': 'completed' if old_specs and new_specs else 'skipped_missing_spec',
            'runtime_tests': 'not_run',
            'overall_note': 'Coverage describes which analysis stages ran; it is not a compatibility guarantee.'
        },
        'risk_score':risk_score, 'risk_level':risk_level,
        'summary': {'old_frontend_files_scanned':fe['files_scanned'],'old_backend_files_scanned':old['files_scanned'],'new_backend_files_scanned':new['files_scanned'],'removed_backend_fields':len(removed),'added_backend_fields':len(added),'removed_routes':len(routes_removed),'contract_findings':len(contract_findings),'findings_total':len(findings),**{f'{k}_findings':v for k,v in severity_counts.items()}},
        'removed_fields':removed, 'added_fields':added, 'removed_routes':routes_removed,
        'potentially_affected_frontend_fields':affected, 'findings':findings,
        'contract_analysis':contract_summary, 'sources':scans, 'warnings':warnings,
        'limitations':[
            'Risk score is a heuristic prioritization score, not a statistically validated probability or compatibility percentage.',
            'OpenAPI comparison only runs when both backend ZIPs include a recognizable OpenAPI/Swagger JSON or YAML specification; it compares declared contracts, not runtime behavior.',
            'Schema comparison supports a useful subset of OpenAPI; advanced composition, callbacks, webhooks, external references and all JSON Schema edge cases may not be fully interpreted.',
            'Mobile source support detects Kotlin/Java Android and Dart/Flutter source files and heuristically extracts API paths and serialized field names; it does not install or execute an APK/AAB.',
            'Source scanning is heuristic and may miss aliases, serializers, dynamic routes, GraphQL, generated contracts, indirect calls and framework-specific behavior.',
            'Authentication policy, database migrations, latency, side effects and real mobile-device behavior are not runtime-tested.',
            'Use automated contract/integration tests and staged rollout before relying on results for production release decisions.'
        ]
    }
