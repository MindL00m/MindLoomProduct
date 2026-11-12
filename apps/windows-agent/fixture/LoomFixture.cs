// "Loom Fixture": a window of known controls for checking the Windows driver against real
// UI Automation (python -m mindloom_agent --driver-selftest drives it). Every change is
// shown in the Status label, so the self-test can read the result back.
//
// Plain C# 5 and Windows Forms on purpose: scripts/driver-selftest.ps1 compiles it with
// the csc.exe that ships with Windows (.NET Framework 4), so no SDK is needed.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

public class FixtureForm : Form
{
    private readonly TextBox name = new TextBox();
    private readonly TextBox password = new TextBox();
    private readonly TextBox notes = new TextBox();
    private readonly CheckBox urgent = new CheckBox();
    private readonly Label items = new Label();
    private readonly Label status = new Label();
    private readonly List<string> list = new List<string>();

    public FixtureForm()
    {
        Text = "Loom Fixture";
        StartPosition = FormStartPosition.Manual;
        Location = new Point(200, 200);
        ClientSize = new Size(340, 330);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;

        // A classic menu bar, like the older business apps the driver is for.
        MenuItem clear = new MenuItem("Clear List", delegate { ClearList(); }, Shortcut.CtrlK);
        MenuItem delete = new MenuItem("Delete Everything", delegate { ClearList(); });
        MenuItem listMenu = new MenuItem("List", new MenuItem[] { clear, delete });
        MenuItem quit = new MenuItem("Exit", delegate { Close(); });
        MenuItem fileMenu = new MenuItem("File", new MenuItem[] { quit });
        Menu = new MainMenu(new MenuItem[] { fileMenu, listMenu });

        int y = 14;
        AddField(name, "Name", ref y);
        password.UseSystemPasswordChar = true;   // a real password field: UI Automation IsPassword
        password.Text = "s3cret-fixture";
        AddField(password, "Password", ref y);
        AddField(notes, "Notes", ref y);

        urgent.Text = "Urgent";
        urgent.AutoSize = true;
        urgent.Location = new Point(16, y);
        urgent.CheckedChanged += delegate { SetStatus(urgent.Checked ? "urgent on" : "urgent off"); };
        Controls.Add(urgent);
        y += 32;

        Button add = new Button();
        add.Text = "Add Item";
        add.Location = new Point(16, y);
        add.Size = new Size(110, 28);
        add.Click += delegate { AddItem(); };
        Controls.Add(add);

        Button send = new Button();
        send.Text = "Send Report";
        send.Location = new Point(136, y);
        send.Size = new Size(110, 28);
        send.Click += delegate { SetStatus("report sent"); };
        Controls.Add(send);
        AcceptButton = send;                     // the default button, so Enter presses it
        y += 44;

        items.Text = "Items: none";
        items.AutoSize = true;
        items.Location = new Point(16, y);
        Controls.Add(items);
        y += 26;

        status.Text = "Status: idle";
        status.AutoSize = true;
        status.Location = new Point(16, y);
        Controls.Add(status);
    }

    // Opened by the self-test without taking the foreground.
    protected override bool ShowWithoutActivation
    {
        get { return true; }
    }

    private void AddField(TextBox box, string label, ref int y)
    {
        Label caption = new Label();
        caption.Text = label + ":";
        caption.AutoSize = true;
        caption.Location = new Point(16, y + 3);
        Controls.Add(caption);
        box.AccessibleName = label;
        box.Location = new Point(96, y);
        box.Width = 220;
        Controls.Add(box);
        y += 34;
    }

    private void SetStatus(string text)
    {
        status.Text = "Status: " + text;
    }

    private void AddItem()
    {
        string value = name.Text.Trim();
        if (value.Length == 0)
        {
            SetStatus("name is empty");
            return;
        }
        list.Add(value);
        items.Text = "Items: " + string.Join(", ", list.ToArray());
        SetStatus("added " + value);
    }

    private void ClearList()
    {
        list.Clear();
        items.Text = "Items: none";
        SetStatus("cleared");
    }

    [STAThread]
    public static void Main()
    {
        Application.EnableVisualStyles();
        Application.Run(new FixtureForm());
    }
}
