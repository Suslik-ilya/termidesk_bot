from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.services.cache_service import CacheService

admin_router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory="app/templates")

@admin_router.get("/", response_class=HTMLResponse)
async def admin_panel(request: Request):
    cache_service = CacheService()
    candidates = cache_service.get_candidates()
    approved = cache_service.get_approved_entries()
    
    return templates.TemplateResponse("admin_panel.html", {
        "request": request,
        "candidates": candidates,
        "approved": approved
    })

@admin_router.post("/approve")
async def approve_candidate(background_tasks: BackgroundTasks, entry_id: str = Form(...)):
    cache_service = CacheService()
    # Асинхронный запуск операции для исключения блокировки UI
    background_tasks.add_task(cache_service.approve_candidate, entry_id)
    return RedirectResponse(url="/admin/", status_code=303)

@admin_router.post("/edit")
async def edit_entry(entry_id: str = Form(...), new_answer: str = Form(...)):
    cache_service = CacheService()
    cache_service.edit_answer(entry_id, new_answer)
    return RedirectResponse(url="/admin/", status_code=303)

@admin_router.post("/delete")
async def delete_entry(entry_id: str = Form(...)):
    cache_service = CacheService()
    cache_service.delete_entry(entry_id)
    return RedirectResponse(url="/admin/", status_code=303)
