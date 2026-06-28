import { request } from "./request";
import type { LocalModelConfig } from "../utils/local-model-config";

export function checkAuthKey(params: { apiKey: string }): Promise<boolean> {
  return request({
    url: `/check-key`,
    params,
  });
}

export type UploadUrlResponse = {
  result: {
    objectKey: string;
    preSignedURL: string;
    imgUrl: string;
  };
  id: number;
  exception: string;
  status: string;
  isCanceled: boolean;
  isCompleted: boolean;
  isCompletedSuccessfully: boolean;
  creationOptions: number;
  asyncState: null;
  isFaulted: boolean;
};

export function getPdfUploadUrl(): Promise<UploadUrlResponse> {
  return request({
    url: `/pdf-upload-url`,
    retries: 3,
  });
}

type UploadPdfRequest = {
  uploadUrl: string;
  file: File;
};

export function uploadPdf(data: UploadPdfRequest) {
  return request({
    url: data.uploadUrl,
    method: "PUT",
    body: data.file,
    responseType: "arraybuffer",
    retries: 3,
    headers: {
      "Content-Type": "application/pdf",
    },
  });
}

export function downloadPdf(url: string) {
  return request({
    url,
    method: "GET",
    retries: 3,
    responseType: "arraybuffer",
  });
}

// 国际站用户健康检查API
export async function checkInternationalServerHealth(): Promise<{
  success: boolean;
  status?: number;
}> {
  return { success: true };
}

type CreateTranslateTaskRequest = {
  objectKey: string;
  pdfOptions: {
    conversion_formats: {
      html: boolean;
    };
  };
  fileName: string;
  // 目标语言
  targetLanguage: string;
  // 翻译服务
  requestModel: string;
  // 兼容模式
  enhance_compatibility: boolean;
  // OCR 解决方案
  OCRWorkaround: boolean;
  // 是否
  autoEnableOcrWorkAround: boolean;
  // 自动提取术语
  autoExtractGlossary: boolean;
  // 禁用富文本翻译
  disable_rich_text_translate: boolean;
  // 字体
  primaryFontFamily: string;
  // 双语对照显示方式
  dual_mode: string;
  // 自定义系统提示词
  customSystemPrompt: string | null;
  // 布局模型ID
  layout_model_id: string;
  // 本地模型配置
  modelConfig?: LocalModelConfig | null;
};

export function createTranslateTask(
  data: CreateTranslateTaskRequest,
): Promise<string> {
  return request({
    url: "/backend-babel-pdf",
    method: "POST",
    body: data,
    retries: 3,
  });
}

type TestLocalModelRequest = {
  requestModel: string;
  targetLanguage: string;
  modelConfig?: LocalModelConfig | null;
};

type TestLocalModelResponse = {
  model: string;
  message: string;
};

export function testLocalModel(
  data: TestLocalModelRequest,
): Promise<TestLocalModelResponse> {
  return request({
    url: "/test-model",
    method: "POST",
    body: data,
  });
}

type GetTranslatePdfCountRequest = {
  objectKey: string;
};

export function getTranslatePdfCount(
  data: GetTranslatePdfCountRequest,
): Promise<string> {
  return request({
    url: `/pdf-count`,
    body: data,
  });
}

type GetTranslateStatusRequest = {
  pdfId: string;
};

type GetTranslateStatusResponse = {
  overall_progress: number;
  currentStageName: string;
  status: string;
  message: string;
  num_pages: number;
};

export function getTranslateStatus(
  data: GetTranslateStatusRequest,
): Promise<GetTranslateStatusResponse> {
  return request({
    url: `/pdf/${data.pdfId}/process`,
    retries: 10,
  });
}

type GetTranslatePdfResultRequest = {
  pdfId: string;
};

type GetTranslatePdfResultResponse = {
  translationOnlyPdfOssUrl: string;
  translationDualPdfOssUrl: string;
  waterMask: boolean;
  monoFileUrl: string;
};

export function getTranslatePdfResult(
  data: GetTranslatePdfResultRequest,
): Promise<GetTranslatePdfResultResponse> {
  return request({
    url: `/pdf/${data.pdfId}/temp-url`,
    retries: 3,
  });
}

type GetRecordListRequest = {
  page?: number;
  pageSize?: number;
};

type GetRecordListResponse = {
  total: number;
  list: {
    createTime: string;
    fileName: string;
    pdfStatus: string;
    recordId: string;
    pageCount: number;
    consumed: boolean;
    backendStatus: string;
    isWaterMark: boolean;
    sourceLanguage?: string;
    targetLanguage: string;
    errMsg?: string;
    detailStatus?: string;
  }[];
};

export function getRecordList(
  params: GetRecordListRequest,
): Promise<GetRecordListResponse> {
  return request({
    url: `/pdf/record-list`,
    params,
  });
}

export default {
  checkAuthKey,
  getPdfUploadUrl,
  createTranslateTask,
  getTranslatePdfCount,
  getTranslateStatus,
  getTranslatePdfResult,
  getRecordList,
  uploadPdf,
  downloadPdf,
  checkInternationalServerHealth,
  testLocalModel,
};
