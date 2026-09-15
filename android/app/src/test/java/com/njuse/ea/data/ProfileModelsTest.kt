package com.njuse.ea.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 空态判定 hasProfileData() 的纯逻辑测试：
 * 全空 → false（展示「暂无数据」），任一核心字段有值 → true（展示画像）。
 * 防止 ProfileViewModel 空态/数据态分支在契约字段增删时静默失真。
 */
class ProfileModelsTest {

    @Test
    fun hasProfileData_allFieldsEmpty_returnsFalse() {
        assertFalse(StatusResponse().hasProfileData())
    }

    @Test
    fun hasProfileData_attachmentStyleOnly_returnsTrue() {
        assertTrue(StatusResponse(attachment_style = "anxious").hasProfileData())
    }

    @Test
    fun hasProfileData_coreFearsOnly_returnsTrue() {
        assertTrue(StatusResponse(core_fears = listOf("被抛弃")).hasProfileData())
    }

    @Test
    fun hasProfileData_personalValuesOnly_returnsTrue() {
        assertTrue(StatusResponse(personal_values = listOf("家庭")).hasProfileData())
    }

    @Test
    fun hasProfileData_distressToleranceOnly_returnsTrue() {
        assertTrue(StatusResponse(distress_tolerance = 0.5f).hasProfileData())
    }

    @Test
    fun hasProfileData_rejectionSensitivityNumericOnly_returnsTrue() {
        assertTrue(StatusResponse(rejection_sensitivity = 0.8f).hasProfileData())
    }

    @Test
    fun hasProfileData_rejectionSensitivityLabelOnly_returnsTrue() {
        assertTrue(StatusResponse(rejection_sensitivity_label = "high").hasProfileData())
    }
}
