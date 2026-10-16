static constant float SENS[4] = {1.0f, 1.52f, 1.61f, 2.0f};

__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, int p_Y, float p_R, float p_G, float p_B) {
    int i = (int)NodeSel; if (i < 0) i = 0; if (i >= NODES) i = NODES-1;
    int s = (int)FootageSel; if (s < 0) s = 0; if (s > 3) s = 3;

    float W = (float)p_Width;
    float H = (float)p_Height;
    // scale the calibration grid by footage_format / profile_format
    float D = FX[i] * W * SENS[s] / SFF[i];

    float cx = CX[i] * (W - 1.0f);
    float cy = CY[i] * (H - 1.0f);
    float dx = (float)p_X - cx;
    float dy = (float)p_Y - cy;

    float rho2 = (dx*dx + dy*dy) / (D*D);
    float V = 1.0f + rho2 * (A1[i] + rho2 * (A2[i] + rho2 * A3[i]));
    // Trusted band of the model: gain capped at 20x. Near the extreme corner
    // of wide lenses the polynomial overshoots toward zero.
    if (V < 0.05f) V = 0.05f;
    if (V > 20.0f) V = 20.0f;

    float t = _clampf(Blend, 0.0f, 1.0f);
    float gain = 1.0f + t * (1.0f / V - 1.0f);

    return make_float3(p_R * gain, p_G * gain, p_B * gain);
}
