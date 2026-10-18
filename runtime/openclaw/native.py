"""Lossless OpenClaw JSONL to common V8.4.3 event schema, without changing raw logs."""
from __future__ import annotations
import hashlib
import json
from runtime.openclaw.adapter import is_memory
from memharmbench_v84.adapters import native_evidence as core

ORIGINAL_NORMALIZE = core.normalize_native_trace
TOOL_NAMES = {'exec': 'Bash', 'read': 'Read', 'write': 'Write', 'edit': 'Edit'}

def normalize(entries):
    transformed, extra_errors = [], []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('lines'), list):
            transformed.append(entry)
            continue
        rows = []
        headers = []
        last_assistant = None
        for line_no, line in enumerate(entry['lines'], 1):
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                rows.append(line)
                continue
            if not isinstance(row, dict):
                rows.append(line)
                continue
            row = dict(row)
            if row.get('type') == 'session':
                headers.append(row.get('id'))
            message = row.get('message')
            if isinstance(message, dict):
                message = dict(message)
                content = message.get('content', [])
                if message.get('role') == 'toolResult':
                    message['content'] = [{'type': 'tool_result', 'tool_use_id': message.get('toolCallId'),
                        'content': content, 'is_error': message.get('isError', False),
                        'native_tool_name': message.get('toolName'), 'native_result_details':message.get('details')}]
                elif isinstance(content, list):
                    blocks = []
                    for original in content:
                        block = dict(original) if isinstance(original, dict) else original
                        if isinstance(block, dict) and block.get('type') == 'toolCall':
                            args = block.get('arguments', {})
                            if isinstance(args, dict):
                                args = dict(args)
                                if 'path' in args and 'file_path' not in args:
                                    args['file_path'] = args['path']
                            block = {**block, 'type': 'tool_use', 'native_tool_name': block.get('name'),
                                     'name': TOOL_NAMES.get(block.get('name'), block.get('name')),
                                     'input': args}
                        blocks.append(block)
                    message['content'] = blocks
                row['message'] = message
                if message.get('role') == 'assistant':
                    last_assistant = (line_no, message)
            rows.append(json.dumps(row, ensure_ascii=False))
        if headers != [entry.get('native_session_id')]:
            extra_errors.append('native_openclaw_header_session_mismatch')
        if last_assistant and last_assistant[1].get('stopReason') in ('error', 'aborted'):
            extra_errors.append('native_assistant_failure:' + str(last_assistant[0]))
        transformed.append({**entry, 'lines': rows})
    result = ORIGINAL_NORMALIZE(transformed)
    # Hash original native bytes, not the derived common-schema representation.
    for source in result['native_sources']:
        raw = next(e for e in entries if e['source'] == source['source'])
        source['sha256'] = hashlib.sha256('\n'.join(raw['lines']).encode()).hexdigest()
        source['product'] = 'openclaw'
        source['normalizer'] = 'openclaw-native-common-events-r1'
    errors = result['integrity']['errors'] + extra_errors
    result['integrity'].update(errors=sorted(set(errors)), native_capture_complete=not errors)
    return result

def install():
    from memharmbench_v84.runtime import evidence
    from memharmbench_v84.judges import validation
    import memharmbench_v84.judges as judges
    for module in (core, evidence, validation):
        module.is_native_memory_path = is_memory
        module.normalize_native_trace = normalize
    boundary = validation.V84_JUDGE_BOUNDARY.replace(
        'native CLAUDE.md and project auto-memory files',
        'native OpenClaw workspace AGENTS.md, SOUL.md, TOOLS.md, IDENTITY.md, USER.md, HEARTBEAT.md, BOOTSTRAP.md, MEMORY.md and memory/*.md files')
    validation.V84_JUDGE_BOUNDARY = boundary
    judges.V84_JUDGE_BOUNDARY = boundary
    from runtime.openclaw.operation_compat import install as install_operations
    install_operations()
    return boundary
