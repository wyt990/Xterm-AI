from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from typing import Annotated
import os

from ssh_handler import SSHHandler
from core import (
    FAILED_OPEN_SFTP_SESSION_DETAIL,
    SERVER_NOT_FOUND_DETAIL,
    SftpChmodModel,
    SftpCreateModel,
    SftpDeleteModel,
    SftpRenameModel,
    SftpSaveModel,
    _get_ssh_config,
    db,
    verify_token,
)


router = APIRouter()


@router.get(
    "/api/sftp/list",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": SERVER_NOT_FOUND_DETAIL},
        500: {"description": FAILED_OPEN_SFTP_SESSION_DETAIL},
    },
)
async def sftp_list(server_id: int, path: str = "/"):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)

    ssh = SSHHandler(**_get_ssh_config(server_info))

    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail=FAILED_OPEN_SFTP_SESSION_DETAIL)

    try:
        if not path or path == "":
            path = sftp.normalize(".")

        files = []
        attr_list = sftp.listdir_attr(path)
        import stat as py_stat

        attr_list.sort(key=lambda x: (not py_stat.S_ISDIR(x.st_mode), x.filename.lower()))

        for attr in attr_list:
            is_dir = py_stat.S_ISDIR(attr.st_mode)
            files.append(
                {
                    "name": attr.filename,
                    "size": attr.st_size if not is_dir else 0,
                    "mode": oct(attr.st_mode)[-4:],
                    "mtime": attr.st_mtime,
                    "is_dir": is_dir,
                    "type": "dir" if is_dir else "file",
                }
            )

        return {"path": path, "files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()


@router.get(
    "/api/sftp/download",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": SERVER_NOT_FOUND_DETAIL},
        500: {"description": FAILED_OPEN_SFTP_SESSION_DETAIL},
    },
)
async def sftp_download(server_id: int, path: str):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)

    ssh = SSHHandler(**_get_ssh_config(server_info))

    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail=FAILED_OPEN_SFTP_SESSION_DETAIL)

    try:
        filename = os.path.basename(path)

        def iter_file():
            with sftp.open(path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 64)
                    if not chunk:
                        break
                    yield chunk
            sftp.close()
            ssh.close()

        return StreamingResponse(
            iter_file(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        if sftp:
            sftp.close()
        ssh.close()
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/api/sftp/upload",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": SERVER_NOT_FOUND_DETAIL},
        500: {"description": FAILED_OPEN_SFTP_SESSION_DETAIL},
    },
)
async def sftp_upload(
    server_id: Annotated[int, Form(...)],
    path: Annotated[str, Form(...)],
    file: Annotated[UploadFile, File(...)],
):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)

    ssh = SSHHandler(**_get_ssh_config(server_info))

    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail=FAILED_OPEN_SFTP_SESSION_DETAIL)

    try:
        remote_path = os.path.join(path, file.filename).replace("\\", "/")
        with sftp.open(remote_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                f.write(chunk)

        return {"status": "success", "path": remote_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()


@router.post(
    "/api/sftp/rename",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP rename failed"}},
)
async def sftp_rename(data: SftpRenameModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        sftp.rename(data.old_path, data.new_path)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()


@router.post(
    "/api/sftp/chmod",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP chmod failed"}},
)
async def sftp_chmod(data: SftpChmodModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        mode_int = int(data.mode, 8)
        sftp.chmod(data.path, mode_int)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()


@router.delete(
    "/api/sftp/delete",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP delete failed"}},
)
async def sftp_delete(data: SftpDeleteModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        if data.is_dir:
            sftp.rmdir(data.path)
        else:
            sftp.remove(data.path)
        return {"status": "success"}
    except Exception as e:
        if data.is_dir:
            try:
                ssh.connect()
                ssh.write(f"rm -rf '{data.path}'\n")
                return {"status": "success", "note": "used ssh rm -rf"}
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()


@router.get(
    "/api/sftp/read",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "文件超过 30MB，无法打开"},
        500: {"description": "SFTP read failed"},
    },
)
async def sftp_read(server_id: int, path: str):
    server_info = db.get_server_by_id(server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        stat = sftp.stat(path)
        size_mb = stat.st_size / (1024 * 1024)

        if size_mb > 30:
            raise HTTPException(status_code=400, detail="文件超过 30MB，无法打开")

        with sftp.open(path, "rb") as f:
            binary_content = f.read()
            content = binary_content.decode("utf-8", errors="replace")

        return {"content": content, "readonly": size_mb > 3, "size": stat.st_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()


@router.post(
    "/api/sftp/save",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP save failed"}},
)
async def sftp_save(data: SftpSaveModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        with sftp.open(data.path, "wb") as f:
            f.write(data.content.encode("utf-8"))
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()


@router.post(
    "/api/sftp/create",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP create failed"}},
)
async def sftp_create(data: SftpCreateModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        if data.type == "dir":
            sftp.mkdir(data.path)
        else:
            with sftp.open(data.path, "w") as f:
                f.write("")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp:
            sftp.close()
        ssh.close()
