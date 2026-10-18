from app.compatibility import analyze_compatibility


def test_removed_field_flags_only_versions_that_expect_it():
    versions = [
        {"version": "1.0", "platform": "android", "expected_fields": ["user_name", "email"]},
        {"version": "2.0", "platform": "android", "expected_fields": ["full_name", "email"]},
    ]
    report = analyze_compatibility(
        versions, ["user_name", "email"], ["full_name", "email"],
        "/api/profile", "Rename user_name to full_name"
    )
    assert report["breaking_change"] is True
    assert report["affected_version_count"] == 1
    assert report["results"][0]["breaking"] is True
    assert report["results"][1]["breaking"] is False


def test_added_field_does_not_flag_breakage():
    report = analyze_compatibility(
        [{"version": "1.0", "expected_fields": ["email"]}],
        ["email"], ["email", "avatar_url"], "/api/profile", "Add avatar"
    )
    assert report["breaking_change"] is False
    assert report["affected_version_count"] == 0


def test_openapi_contract_diff_detects_removed_field_and_type_change():
    import io
    import json
    import zipfile
    from app.analyzer import analyze_archives

    def archive(files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, body in files.items():
                zf.writestr(name, body)
        return stream.getvalue()

    def spec(properties, required):
        return {
            "openapi": "3.0.0",
            "paths": {
                "/api/users/{id}": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {"application/json": {"schema": {
                                    "type": "object", "required": required, "properties": properties
                                }}}
                            }
                        }
                    }
                }
            }
        }
    old_spec = spec({"id": {"type": "string"}, "user_name": {"type": "string"}, "age": {"type": "integer"}}, ["id", "user_name"])
    new_spec = spec({"id": {"type": "string"}, "full_name": {"type": "string"}, "age": {"type": "string"}}, ["id"])
    frontend = archive({'src/api.ts': 'const name = response.user_name;'})
    old_backend = archive({'app.py': '@app.get("/api/users/{id}")', 'openapi.json': json.dumps(old_spec)})
    new_backend = archive({'app.py': '@app.get("/api/users/{id}")', 'openapi.json': json.dumps(new_spec)})
    result = analyze_archives(frontend, old_backend, new_backend)
    categories = {finding['category'] for finding in result['findings']}
    assert result['contract_analysis']['operations_compared'] == 1
    assert 'response_field_removed' in categories
    assert 'response_type_changed' in categories
    assert result['risk_level'] in {'moderate', 'high', 'critical'}


def test_openapi_detects_optional_parameter_becoming_required_and_body_required():
    import io, json, zipfile
    from app.analyzer import analyze_archives

    def archive(files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, body in files.items(): zf.writestr(name, body)
        return stream.getvalue()

    def spec(required_param, required_body):
        return {
            'openapi': '3.0.0',
            'paths': {'/api/search': {'get': {
                'parameters': [{'in': 'query', 'name': 'page', 'required': required_param, 'schema': {'type': 'integer'}}],
                'requestBody': {'required': required_body, 'content': {'application/json': {'schema': {'type': 'object', 'properties': {'q': {'type': 'string'}}}}}},
                'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'string'}}}}}}}}
            }}}
        }
    fe = archive({'src/api.ts': 'const items = response.items;'})
    old = archive({'app.py': 'app.get("/api/search")', 'openapi.json': json.dumps(spec(False, False))})
    new = archive({'app.py': 'app.get("/api/search")', 'openapi.json': json.dumps(spec(True, True))})
    result = analyze_archives(fe, old, new)
    categories = {f['category'] for f in result['findings']}
    assert 'required_parameter_added' in categories
    assert 'request_body_now_required' in categories
    assert result['analysis_coverage']['runtime_tests'] == 'not_run'


def test_openapi_detects_enum_narrowing_and_array_item_type_change():
    import io, json, zipfile
    from app.analyzer import analyze_archives

    def archive(files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, body in files.items(): zf.writestr(name, body)
        return stream.getvalue()

    def spec(status_enum, item_type):
        return {'openapi': '3.0.0', 'paths': {'/api/orders': {'get': {'responses': {'200': {
            'description': 'ok', 'content': {'application/json': {'schema': {'type': 'object', 'required': ['status', 'tags'], 'properties': {
                'status': {'type': 'string', 'enum': status_enum},
                'tags': {'type': 'array', 'items': {'type': item_type}}
            }}}}
        }}}}}}
    fe = archive({'src/api.ts': 'const status = response.status; const tags = response.tags;'})
    old = archive({'app.py': 'app.get("/api/orders")', 'openapi.json': json.dumps(spec(['open', 'closed', 'pending'], 'string'))})
    new = archive({'app.py': 'app.get("/api/orders")', 'openapi.json': json.dumps(spec(['open', 'closed'], 'integer'))})
    result = analyze_archives(fe, old, new)
    categories = {f['category'] for f in result['findings']}
    assert 'response_enum_narrowed' in categories
    assert 'response_array_item_type_changed' in categories


def test_source_fallback_reports_openapi_skipped_without_claiming_runtime_coverage():
    import io, zipfile
    from app.analyzer import analyze_archives

    def archive(files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, body in files.items(): zf.writestr(name, body)
        return stream.getvalue()

    fe = archive({'src/api.ts': 'const name = response.user_name;'})
    old = archive({'app.py': '@app.get("/api/users")\nreturn {"user_name": str}'})
    new = archive({'app.py': '@app.get("/api/users")\nreturn {"full_name": str}'})
    result = analyze_archives(fe, old, new)
    assert result['analysis_coverage']['source_scan'] == 'completed'
    assert result['analysis_coverage']['openapi_contract'] == 'skipped_missing_spec'
    assert result['analysis_coverage']['runtime_tests'] == 'not_run'


def test_swagger_v2_definitions_and_body_parameter_are_supported():
    import io, json, zipfile
    from app.analyzer import analyze_archives

    def archive(files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, body in files.items(): zf.writestr(name, body)
        return stream.getvalue()

    def spec(field_name, required_body):
        return {
            'swagger': '2.0', 'info': {'title': 'Demo', 'version': '1.0'},
            'consumes': ['application/json'], 'produces': ['application/json'],
            'definitions': {'Payload': {'type': 'object', 'required': [field_name], 'properties': {field_name: {'type': 'string'}}}},
            'paths': {'/api/profile': {'post': {
                'parameters': [{'name': 'body', 'in': 'body', 'required': required_body, 'schema': {'$ref': '#/definitions/Payload'}}],
                'responses': {'200': {'description': 'ok', 'schema': {'$ref': '#/definitions/Payload'}}}
            }}}
        }
    fe = archive({'src/api.ts': 'const name = response.user_name;'})
    old = archive({'app.py': 'route', 'swagger.json': json.dumps(spec('user_name', False))})
    new = archive({'app.py': 'route', 'swagger.json': json.dumps(spec('full_name', True))})
    result = analyze_archives(fe, old, new)
    categories = {f['category'] for f in result['findings']}
    assert result['contract_analysis']['operations_compared'] == 1
    assert 'request_body_now_required' in categories
    assert 'response_field_removed' in categories
