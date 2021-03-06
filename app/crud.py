import abc
from typing import (
    TypeVar,
    Generic,
    Type,
    Optional,
    Dict,
    Sequence,
)

import jsonpatch
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from pymongo import ReturnDocument

from app.models import QuizModel, RoomModel

SNOWFLAKE = int

ModelType = TypeVar("ModelType", bound=BaseModel)
PatchType = TypeVar("PatchType", bound=BaseModel)


class _CRUDBase(Generic[ModelType], metaclass=abc.ABCMeta):
    def __init__(self, collection: str, model: Type[ModelType]):
        self.collection = collection
        self.model = model

    async def create(self, db: AsyncIOMotorClient, *, obj_in: ModelType) -> Dict:
        obj_in_data = jsonable_encoder(obj_in)
        new_obj = await db[self.collection].insert_one(obj_in_data)
        return await db[self.collection].find_one({"_id": new_obj.inserted_id})

    async def get(self, db: AsyncIOMotorClient, *, id_: SNOWFLAKE) -> Optional[Dict]:
        return await db[self.collection].find_one({"_id": id_})

    async def patch(self, db, *, original: Dict, json_patch_request):
        # Todo: this whole process smells

        # I have had a lot of trouble trying to type these patch requests that I
        # decided to do it manually
        if not isinstance(json_patch_request, list):
            raise ValueError("A jsonpatch request must be a list")

        # Some fields are not allowed to be changed; make sure that they aren't accessed
        patches = jsonpatch.JsonPatch(json_patch_request)
        self._ensure_fields_are_not_blacklisted(patches)

        patched_obj = patches.apply(original, in_place=False)
        # Replace any "ids" with "_ids" (Snowflakes)
        quiz_model = self.model(**patched_obj)
        new_quiz = quiz_model.dict(by_alias=True)

        # We have to make sure that no one has edited the original while we were processing stuff here,
        # so we filter against the entire original, not just the _id
        # Todo: Error message/try-again
        return await db[self.collection].find_one_and_replace(
            original, new_quiz, return_document=ReturnDocument.AFTER
        )

    # Todo: blacklisted isn't a good description. Find a better word
    @property
    @abc.abstractmethod
    def blacklisted_paths(self) -> Sequence[Optional[str]]:
        return NotImplemented

    def _ensure_fields_are_not_blacklisted(self, patches: jsonpatch.JsonPatch):
        for p in patches:
            pointer = jsonpatch.JsonPointer(p["path"])
            pointer_path = pointer.path
            if pointer_path == "/":
                # We do not allow empty strings as keys
                raise ValueError
            for path in self.blacklisted_paths:
                if path in pointer_path:
                    raise ValueError(f"{pointer_path} disallows patching {path}")

    async def delete(self, db: AsyncIOMotorClient, *, id_: SNOWFLAKE) -> bool:
        delete_result = await db[self.collection].delete_one({"_id": id_})
        if delete_result.deleted_count == 1:
            return True
        return False  # Not found


########################################################################################
class CRUDQuiz(_CRUDBase[QuizModel]):
    @property
    def blacklisted_paths(self) -> Sequence[Optional[str]]:
        return "owner", "id", "_id"

    async def get_titles_by_user(self, db: AsyncIOMotorClient, *, user_id: SNOWFLAKE):
        return (
            await db[self.collection]
            .find({"owner": user_id}, {"_id": 1, "title": 1})
            .to_list(None)
        )


class CRUDRoom(_CRUDBase[RoomModel]):
    @property
    def blacklisted_paths(self) -> Sequence[Optional[str]]:
        return "_id", "id", "code", "owner_id", "quiz_id"

    async def get_by_code(self, db: AsyncIOMotorClient, *, code: str) -> Optional[Dict]:
        return await db[self.collection].find_one({"code": code, "is_active": True})


quiz = CRUDQuiz("quizzes", QuizModel)
room = CRUDRoom("rooms", RoomModel)
