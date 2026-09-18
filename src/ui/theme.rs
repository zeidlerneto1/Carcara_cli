use ratatui::style::{Color, Modifier, Style};

/// Tema dark/light com paletas por componente (espelhando o theme.py do Carcara).
#[derive(Debug, Clone, Copy)]
pub struct Theme {
    pub name: &'static str,
    // Cores base
    pub input_header: Color,
    pub input_header_plan: Color,
    pub prompt_symbol: Color,
    pub assistant_text: Color,
    pub assistant_bullet: Color,
    pub thinking: Color,
    pub tool_name: Color,
    pub tool_args: Color,
    pub tool_ok_bullet: Color,
    pub tool_err_bullet: Color,
    pub status: Color,
    pub error: Color,
    pub warn: Color,
    pub success: Color,
    pub info: Color,
    pub plan_border: Color,
    pub toolbar_flag_yolo: Color,
    pub toolbar_flag_afk: Color,
    pub toolbar_flag_plan: Color,
    pub toolbar_dim: Color,
    // Diff
    pub diff_add_bg: Color,
    pub diff_del_bg: Color,
    pub diff_add_fg: Color,
    pub diff_del_fg: Color,
    // Banner
    pub banner_border: Color,
    pub banner_grey: Color,
    // Background
    pub bg: Color,
    pub fg: Color,
}

pub const DARK: Theme = Theme {
    name: "dark",
    input_header: Color::Rgb(128, 128, 128),
    input_header_plan: Color::Rgb(0, 170, 255),
    prompt_symbol: Color::Rgb(255, 220, 0),
    assistant_text: Color::White,
    assistant_bullet: Color::Rgb(128, 128, 128),
    thinking: Color::Rgb(150, 150, 150),
    tool_name: Color::Blue,
    tool_args: Color::Rgb(128, 128, 128),
    tool_ok_bullet: Color::Green,
    tool_err_bullet: Color::Red,
    status: Color::Rgb(128, 128, 128),
    error: Color::Red,
    warn: Color::Yellow,
    success: Color::Green,
    info: Color::Cyan,
    plan_border: Color::Cyan,
    toolbar_flag_yolo: Color::Rgb(255, 255, 0),
    toolbar_flag_afk: Color::Rgb(255, 136, 0),
    toolbar_flag_plan: Color::Rgb(0, 170, 255),
    toolbar_dim: Color::Rgb(128, 128, 128),
    diff_add_bg: Color::Rgb(0x12, 0x26, 0x1e),
    diff_del_bg: Color::Rgb(0x2d, 0x12, 0x14),
    diff_add_fg: Color::Green,
    diff_del_fg: Color::Red,
    banner_border: Color::Rgb(28, 151, 234),
    banner_grey: Color::Rgb(128, 128, 128),
    bg: Color::Reset,
    fg: Color::Reset,
};

pub const LIGHT: Theme = Theme {
    name: "light",
    input_header: Color::Rgb(128, 128, 128),
    input_header_plan: Color::Rgb(0, 130, 220),
    prompt_symbol: Color::Rgb(180, 150, 0),
    assistant_text: Color::Black,
    assistant_bullet: Color::Rgb(90, 90, 90),
    thinking: Color::Rgb(90, 90, 90),
    tool_name: Color::Blue,
    tool_args: Color::Rgb(90, 90, 90),
    tool_ok_bullet: Color::Green,
    tool_err_bullet: Color::Red,
    status: Color::Rgb(90, 90, 90),
    error: Color::Red,
    warn: Color::Rgb(180, 130, 0),
    success: Color::Green,
    info: Color::Cyan,
    plan_border: Color::Cyan,
    toolbar_flag_yolo: Color::Rgb(160, 140, 0),
    toolbar_flag_afk: Color::Rgb(200, 110, 0),
    toolbar_flag_plan: Color::Rgb(0, 110, 200),
    toolbar_dim: Color::Rgb(90, 90, 90),
    diff_add_bg: Color::Rgb(0xda, 0xfb, 0xe1),
    diff_del_bg: Color::Rgb(0xff, 0xeb, 0xe9),
    diff_add_fg: Color::Green,
    diff_del_fg: Color::Red,
    banner_border: Color::Rgb(28, 151, 234),
    banner_grey: Color::Rgb(90, 90, 90),
    bg: Color::Reset,
    fg: Color::Reset,
};

/// Estilos de conveniência derivados do tema.
impl Theme {
    pub fn dim(&self) -> Style {
        Style::default().fg(self.toolbar_dim)
    }
    pub fn tool_ok_style(&self) -> Style {
        Style::default().fg(self.tool_ok_bullet)
    }
    pub fn tool_err_style(&self) -> Style {
        Style::default().fg(self.tool_err_bullet)
    }
    pub fn error_style(&self) -> Style {
        Style::default().fg(self.error)
    }
    pub fn bold(&self, color: Color) -> Style {
        Style::default().fg(color).add_modifier(Modifier::BOLD)
    }
}
