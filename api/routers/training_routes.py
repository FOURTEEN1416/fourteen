from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Security

from api.state import TrainingStateManager

logger = logging.getLogger("rest_api.training")

router = APIRouter(prefix="/api", tags=["training"])

_orch = None
_verify_api_key = None

_training_mgr = TrainingStateManager()


def set_dependencies(orch, verify_api_key):
    global _orch, _verify_api_key
    _orch = orch
    _verify_api_key = verify_api_key


@router.get("/training/status")
async def training_status(_auth: bool = Security(_verify_api_key)):
    try:
        import clone_training  # noqa: F401
        available = True
        desc = "训练管线已就绪"
    except ImportError:
        available = False
        desc = "训练模块未安装"
    return {"available": available, "description": desc, "steps": [
        "data_extract", "style_analyze", "build_dataset", "lora_train", "export_model",
    ]}


@router.post("/training/extract")
async def start_extraction(target: str = "", source: str = "wcf", _auth: bool = Security(_verify_api_key)):
    def _do_extract():
        try:
            from weclone_adapter import WeCloneAdapter
            adapter = WeCloneAdapter(
                data_dir=str(Path(__file__).parent.parent.parent / "data" / "clone"),
                output_dir=str(Path(__file__).parent.parent.parent / "data" / "training"),
            )
            result = adapter.extract(target=target, source=source)
            _training_mgr.update(
                status="extracted",
                extracted_turns=len(result) if isinstance(result, list) else result.get("turns", 0),
                progress=0.3,
                step_name="数据提取",
            )
        except Exception:
            logger.exception("Extraction failed")
            _training_mgr.update(status="error", error="internal_error")

    if not target.strip():
        raise HTTPException(status_code=400, detail="target is required")

    _training_mgr.update(status="extracting", start_time=time.time(), step_name="数据提取")
    _training_mgr.submit(_do_extract)

    return {"status": "started", "task": "extract", "target": target}


@router.get("/training/progress")
async def get_training_progress(_auth: bool = Security(_verify_api_key)):
    return _training_mgr.get_state()


@router.post("/training/clean")
async def start_cleaning(accept_score: int = 2, _auth: bool = Security(_verify_api_key)):
    def _do_clean():
        try:
            from clone_training.data_cleaner import DataCleaner
            from llm_provider import get_llm
            llm = get_llm()
            cleaner = DataCleaner(llm=llm, accept_score=accept_score)
            data_dir = Path(__file__).parent.parent.parent / "data" / "training"
            json_files = sorted(data_dir.glob("*.jsonl"))
            if not json_files:
                raise FileNotFoundError("No dataset found")
            latest = str(json_files[-1])
            result_path = cleaner.score_from_dataset(latest)
            cleaned_count = 0
            if result_path:
                try:
                    with open(result_path, encoding="utf-8") as f:
                        cleaned_data = json.load(f)
                    cleaned_count = len(cleaned_data) if isinstance(cleaned_data, list) else 0
                except Exception:  # noqa: BLE001
                    pass
            _training_mgr.update(
                status="cleaned",
                cleaned_turns=cleaned_count,
                progress=0.6,
                step_name="数据清洗",
            )
        except Exception:
            logger.exception("Cleaning failed")
            _training_mgr.update(status="error", error="internal_error")

    _training_mgr.update(status="cleaning", start_time=time.time(), step_name="数据清洗")
    _training_mgr.submit(_do_clean)

    return {"status": "started", "task": "clean", "accept_score": accept_score}


@router.post("/training/train")
async def start_training(epochs: int = 3, lora_rank: int = 16, _auth: bool = Security(_verify_api_key)):
    def _do_train():
        try:
            from weclone_adapter import WeCloneAdapter
            adapter = WeCloneAdapter(
                data_dir=str(Path(__file__).parent.parent.parent / "data" / "clone"),
                output_dir=str(Path(__file__).parent.parent.parent / "data" / "training"),
            )

            def progress_callback(step, total, loss):
                _training_mgr.update(
                    current_step=step,
                    total_steps=total,
                    progress=step / total if total > 0 else 0,
                    loss=loss,
                )
                if _training_mgr.is_stopping:
                    raise InterruptedError("Training stopped by user")

            result = adapter.train(
                config_path="",
                progress_callback=progress_callback,
                epochs=epochs,
                lora_rank=lora_rank,
            )
            _training_mgr.update(
                status="done" if result.get("status") == "success" else "error",
                progress=1.0,
                step_name="模型训练",
            )
            if "lora_path" in result:
                _training_mgr.update(lora_path=result["lora_path"])
        except InterruptedError:
            _training_mgr.update(status="stopped")
        except Exception:
            logger.exception("Training failed")
            _training_mgr.update(status="error", error="internal_error")

    _training_mgr.update(status="training", start_time=time.time(), step_name="模型训练")
    _training_mgr.submit(_do_train)

    return {"status": "started", "task": "train", "epochs": epochs}


@router.post("/training/stop")
async def stop_training(_auth: bool = Security(_verify_api_key)):
    _training_mgr.stop()
    return {"status": "stopped"}


@router.post("/training/test")
async def test_clone(message: str, _auth: bool = Security(_verify_api_key)):
    try:
        from my_character.tone_mimic import ToneMimic
        chroma_path = str(Path(__file__).parent.parent.parent / "data" / "chroma_db")
        mimic = ToneMimic(chroma_path=chroma_path)
        style_prompt = mimic.get_style_prompt()
        return {"message": message, "style_output": style_prompt, "status": "ok"}
    except (ImportError, OSError, ValueError):
        logger.exception("Test clone failed")
        return {"message": message, "style_output": "", "status": "error", "detail": "internal_error"}


@router.post("/training/apply")
async def apply_clone(_auth: bool = Security(_verify_api_key)):
    try:
        result_path = str(Path(__file__).parent.parent.parent / "data" / "training")
        return {"status": "applied", "path": result_path}
    except (ValueError, OSError):
        logger.exception("Apply clone failed")
        raise HTTPException(status_code=500, detail="internal_error") from None


# ═══ Clone Data Management API ═══

_clone_mgr = None


def _get_clone_mgr():
    global _clone_mgr
    if _clone_mgr is None:
        from api.clone_manager import CloneDataManager
        _clone_mgr = CloneDataManager()
    return _clone_mgr


@router.get("/clone/contacts")
async def list_clone_contacts(keyword: str = "", _auth: bool = Security(_verify_api_key)):
    mgr = _get_clone_mgr()
    contacts = mgr.get_contacts(keyword=keyword)
    return {"contacts": contacts, "total": len(contacts)}


@router.get("/clone/datasets")
async def list_clone_datasets(_auth: bool = Security(_verify_api_key)):
    mgr = _get_clone_mgr()
    datasets = mgr.list_datasets()
    return {"datasets": datasets, "total": len(datasets)}


@router.get("/clone/datasets/{person_id}")
async def get_clone_dataset_detail(
    person_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    only_user: bool = Query(default=False),
    _auth: bool = Security(_verify_api_key),
):
    mgr = _get_clone_mgr()
    return mgr.get_dataset_detail(
        person_id=person_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        only_user=only_user,
    )


@router.delete("/clone/datasets/{person_id}")
async def delete_clone_dataset(person_id: str, _auth: bool = Security(_verify_api_key)):
    mgr = _get_clone_mgr()
    ok = mgr.delete_dataset(person_id)
    if not ok:
        raise HTTPException(404, f"数据集 {person_id} 未找到")
    return {"status": "deleted", "person_id": person_id}


@router.delete("/clone/datasets/{person_id}/conversation")
async def delete_clone_conversation(
    person_id: str,
    index: int = Query(..., description="对话索引（从0开始）"),
    _auth: bool = Security(_verify_api_key),
):
    mgr = _get_clone_mgr()
    ok = mgr.delete_conversation(person_id, index)
    if not ok:
        raise HTTPException(404, "对话未找到或删除失败")
    return {"status": "deleted", "person_id": person_id, "index": index}


@router.post("/clone/datasets/{person_id}/conversations/batch-delete")
async def batch_delete_clone_conversations(
    person_id: str,
    indices: list[int] = Query(..., description="要删除的索引列表"),  # noqa: B008
    _auth: bool = Security(_verify_api_key),
):
    mgr = _get_clone_mgr()
    deleted = mgr.batch_delete_conversations(person_id, indices)
    return {"status": "deleted", "person_id": person_id, "deleted_count": deleted}


@router.get("/clone/stats")
async def get_clone_stats(_auth: bool = Security(_verify_api_key)):
    mgr = _get_clone_mgr()
    return mgr.get_stats()


def get_training_mgr():
    return _training_mgr
