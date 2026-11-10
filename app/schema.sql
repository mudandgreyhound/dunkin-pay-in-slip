-- dunkin-pay-in-slip — ตารางเก็บข้อมูลใบนำฝาก (payin_*)
-- วางใน schema GD_Dashboard, ไม่แตะตารางเดิม (dim_shop, shop_location อ่านอย่างเดียว)
-- ปลอดภัยถ้ารันซ้ำ: ใช้ CREATE TABLE IF NOT EXISTS

-- 1) ใบนำฝาก 1 รายการ = 1 submission (ต่อ 1 วัน/ร้าน)
CREATE TABLE IF NOT EXISTS payin_submission (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  shop_code      VARCHAR(20)     NOT NULL,
  biz_date       DATE            NULL        COMMENT 'วันที่ยอดขาย (Day End) ถ้าอ่านได้',
  cash_sales     DECIMAL(12,2)   NULL        COMMENT 'Cash Sales จาก Day End = เงินสดที่ต้องนำฝาก',
  deposit_total  DECIMAL(12,2)   NULL        COMMENT 'ยอดรวมจากใบนำฝาก',
  diff           DECIMAL(12,2)   NULL        COMMENT 'deposit_total - cash_sales (บวก=เกิน ลบ=ขาด)',
  deposit_channel VARCHAR(40)    NULL        COMMENT 'ช่องทางฝาก: bank/cpaxtra/seven/...',
  gps_lat        DECIMAL(10,7)   NULL,
  gps_lng        DECIMAL(10,7)   NULL,
  distance_m     INT             NULL        COMMENT 'ระยะห่างจากพิกัดร้าน (เมตร)',
  within_radius  TINYINT         NULL        COMMENT '1=อยู่ในรัศมีร้าน, 0=นอกรัศมี',
  status         VARCHAR(20)     NOT NULL DEFAULT 'submitted',
  note           TEXT            NULL,
  created_by     VARCHAR(40)     NULL        COMMENT 'username ที่ส่ง (GDxxxx)',
  created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_shop_date (shop_code, biz_date),
  KEY idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='pay-in-slip: ใบนำฝากที่พนักงานส่งเข้ามา';

-- 2) รูปแต่ละใบ (Day End / ใบนำฝาก) + ผล OCR + location ตอนถ่าย
CREATE TABLE IF NOT EXISTS payin_image (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  submission_id  BIGINT UNSIGNED NULL        COMMENT 'ผูกกับ payin_submission.id',
  shop_code      VARCHAR(20)     NOT NULL,
  kind           VARCHAR(20)     NOT NULL     COMMENT 'day_end | deposit',
  file_path      VARCHAR(300)    NULL         COMMENT 'ที่อยู่ไฟล์รูปบนเซิร์ฟเวอร์',
  amount         DECIMAL(12,2)   NULL         COMMENT 'ยอดเงินที่ OCR ดึงได้จากรูปนี้',
  ocr_text       MEDIUMTEXT      NULL         COMMENT 'ข้อความดิบจาก OCR',
  ocr_json       JSON            NULL         COMMENT 'ฟิลด์ที่ parse ได้',
  gps_lat        DECIMAL(10,7)   NULL,
  gps_lng        DECIMAL(10,7)   NULL,
  gps_accuracy_m INT             NULL,
  distance_m     INT             NULL         COMMENT 'ระยะห่างจากพิกัดร้านตอนถ่าย (เมตร)',
  within_radius  TINYINT         NULL,
  captured_at    DATETIME        NULL         COMMENT 'เวลาถ่าย (จากเครื่องผู้ใช้)',
  created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_submission (submission_id),
  KEY idx_shop_kind (shop_code, kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='pay-in-slip: รูปใบเสร็จ/ใบนำฝาก + ผล OCR + พิกัด';
