from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from src.config.project import SPEC
from src.constants.enums import Role
from src.controllers.views import observation_view
from src.models.base import Session
from src.models.workflow import Observation
from src.services.auth import current_user, roles
from src.services.errors import BusinessError
from src.services.ledger import ledger_view
from src.services.observations import add_observations, clean_observation, parse_csv
from src.utils.formatting import csv_text

router = APIRouter(prefix='/api')
editor = roles(Role.ANALYST, Role.SUPERVISOR)


@router.get('/spec')
def spec():
    return SPEC


@router.get('/observations')
def index(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user=Depends(current_user)):
    with Session() as db:
        total = db.scalar(select(func.count(Observation.id)))
        items = db.scalars(select(Observation).order_by(Observation.id.desc()).offset(offset).limit(limit))
        return {'total': total, 'offset': offset, 'items': [observation_view(item) for item in items]}


@router.get('/observations/ledger')
def ledger(user=Depends(current_user)):
    with Session() as db:
        return ledger_view(db)


@router.post('/observations', status_code=201)
def create(payload: dict, user=Depends(editor)):
    row = clean_observation(payload)
    with Session.begin() as db:
        items, stats = add_observations(db, [row], user['sub'], 'observation.created')
        if not items:
            raise BusinessError('相同观测已存在，请核对记录', 409)
        body = observation_view(items[0])
        body['stats'] = stats
        return body


@router.get('/observations/template')
def template(user=Depends(current_user)):
    fields = SPEC['fields']
    payload = csv_text([{'key': field['key'], 'label': field['key']} for field in fields],
                       [{field['key']: field.get('example') for field in fields}])
    return Response(payload, media_type='text/csv', headers={'Content-Disposition': f'attachment; filename={SPEC["slug"]}-template.csv'})


def import_payload(payload, actor):
    rows = parse_csv(payload)
    with Session.begin() as db:
        _items, stats = add_observations(db, rows, actor, 'observation.imported')
        return stats


@router.post('/observations/import')
async def import_csv(request: Request, user=Depends(editor)):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 512 * 1024:
            raise BusinessError('CSV 文件上限 512 KB', 413)
        raw.extend(chunk)
    return await run_in_threadpool(import_payload, bytes(raw), user['sub'])
