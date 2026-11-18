"""HTML pages. They are thin shells; all data arrives through the REST API."""
from flask import Blueprint, g, redirect, render_template

from .security import HOME_FOR_ROLE, page_roles

bp = Blueprint("pages", __name__)

NAV = {
    "customer": [
        ("dashboard", "Home", "home", "/dashboard"),
        ("activity", "Activity", "list", "/activity"),
        ("transfer", "Send", "send", "/transfer"),
        ("reports", "Reports", "chart", "/reports"),
        ("profile", "More", "user", "/profile"),
    ],
    "staff": [
        ("staff", "Desk", "desk", "/staff"),
        ("profile", "More", "user", "/profile"),
    ],
    "admin": [
        ("admin", "Admin", "shield", "/admin"),
        ("staff", "Desk", "desk", "/staff"),
        ("profile", "More", "user", "/profile"),
    ],
}


def show(template, page, title):
    nav = NAV.get(g.user["role"], []) if g.user else []
    return render_template(template, page=page, title=title, nav=nav)


@bp.get("/")
def home():
    return redirect(HOME_FOR_ROLE[g.user["role"]] if g.user else "/login")


@bp.get("/login")
def login():
    if g.user:
        return redirect(HOME_FOR_ROLE[g.user["role"]])
    return show("login.html", "login", "Sign in")


@bp.get("/register")
def register():
    if g.user:
        return redirect(HOME_FOR_ROLE[g.user["role"]])
    return show("register.html", "register", "Open an account")


def _page(path, endpoint, template, title, *roles):
    @page_roles(*roles)
    def view():
        return show(template, endpoint, title)
    view.__name__ = endpoint
    bp.add_url_rule(path, endpoint, view)


_page("/dashboard", "dashboard", "dashboard.html", "Home", "customer")
_page("/activity", "activity", "activity.html", "Activity", "customer")
_page("/transfer", "transfer", "transfer.html", "Send money", "customer")
_page("/reports", "reports", "reports.html", "Reports", "customer")
_page("/notifications", "notifications", "notifications.html", "Notifications", "customer")
_page("/profile", "profile", "profile.html", "More", "customer", "staff", "admin")
_page("/staff", "staff", "staff.html", "Service desk", "staff", "admin")
_page("/admin", "admin", "admin.html", "Administration", "admin")
