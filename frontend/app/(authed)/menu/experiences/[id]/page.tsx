"use client";
// /menu/experiences/[id] 編輯手沖體驗卡。
import { useParams } from "next/navigation";

import { ExperienceEditor } from "../ExperienceEditor";

export default function EditExperiencePage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  return <ExperienceEditor experienceId={Number.isInteger(id) && id > 0 ? id : -1} />;
}
