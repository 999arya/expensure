from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.views.decorators.http import require_POST

from accounts.models import BankAccount

from .forms import StatementUploadForm
from .models import ImportBatch, ImportRow
from .parsers.statement_csv import MAX_STATEMENT_BYTES, StatementParseError
from .services import cancel_batch, commit_batch, stage_statement, undo_batch


@login_required
def upload(request):
    accounts = BankAccount.objects.filter(owner=request.user, is_active=True)
    if not accounts.exists():
        messages.info(request, "Add an account before importing a statement.")
        return redirect("accounts:add")

    if request.method == "POST":
        form = StatementUploadForm(request.POST, request.FILES, owner=request.user)
        if form.is_valid():
            upload_file = form.cleaned_data["statement"]
            data = upload_file.read(MAX_STATEMENT_BYTES + 1)
            if len(data) > MAX_STATEMENT_BYTES:
                form.add_error("statement", "Keep the statement under 2 MB.")
            else:
                try:
                    batch = stage_statement(
                        request.user,
                        form.cleaned_data["account"],
                        upload_file.name,
                        data,
                    )
                except StatementParseError as exc:
                    form.add_error("statement", str(exc))
                else:
                    messages.success(
                        request,
                        f"Read {batch.row_count} row{pluralize(batch.row_count)}. "
                        "Check the preview before importing.",
                    )
                    return redirect("ingestion:preview", pk=batch.pk)
    else:
        form = StatementUploadForm(owner=request.user)

    recent = ImportBatch.objects.filter(owner=request.user).select_related("account")[:5]
    return render(
        request,
        "ingestion/upload.html",
        {"form": form, "recent_batches": recent},
    )


@login_required
def preview(request, pk):
    batch = get_object_or_404(
        ImportBatch.objects.select_related("account"),
        pk=pk,
        owner=request.user,
    )
    if batch.status != ImportBatch.Status.PARSED:
        return redirect("ingestion:batch_detail", pk=batch.pk)

    rows = batch.rows.all()
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    context = {
        "batch": batch,
        "rows": page.object_list,
        "page_obj": page,
        "ready_count": batch.rows.filter(status=ImportRow.Status.READY).count(),
        "duplicate_count": batch.rows.filter(status=ImportRow.Status.DUPLICATE).count(),
        "invalid_count": batch.rows.filter(status=ImportRow.Status.INVALID).count(),
    }
    return render(request, "ingestion/preview.html", context)


@require_POST
@login_required
def confirm(request, pk):
    batch = get_object_or_404(
        ImportBatch.objects.select_related("account"),
        pk=pk,
        owner=request.user,
        status=ImportBatch.Status.PARSED,
    )
    batch = commit_batch(batch)
    messages.success(
        request,
        f"Imported {batch.imported_count} transaction"
        f"{pluralize(batch.imported_count)}. "
        f"{batch.duplicate_count} duplicate"
        f"{pluralize(batch.duplicate_count)} skipped, "
        f"{batch.invalid_count} row"
        f"{pluralize(batch.invalid_count)} needed attention.",
    )
    return redirect("ingestion:batch_detail", pk=batch.pk)


@require_POST
@login_required
def cancel(request, pk):
    batch = get_object_or_404(
        ImportBatch.objects.select_related("account"),
        pk=pk,
        owner=request.user,
        status=ImportBatch.Status.PARSED,
    )
    cancel_batch(batch)
    messages.info(request, "Import cancelled. No transactions were created.")
    return redirect("ingestion:history")


@login_required
def history(request):
    batches = (
        ImportBatch.objects.filter(owner=request.user)
        .select_related("account")
        .order_by("-created_at", "-id")
    )
    page = Paginator(batches, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "ingestion/history.html",
        {"batches": page.object_list, "page_obj": page},
    )


@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(
        ImportBatch.objects.select_related("account"),
        pk=pk,
        owner=request.user,
    )
    if batch.status == ImportBatch.Status.PARSED:
        return redirect("ingestion:preview", pk=batch.pk)

    transactions = batch.transactions.select_related("account", "category").order_by(
        "-occurred_at", "-id"
    )
    return render(
        request,
        "ingestion/batch_detail.html",
        {"batch": batch, "transactions": transactions},
    )


@require_POST
@login_required
def undo(request, pk):
    batch = get_object_or_404(
        ImportBatch.objects.select_related("account"),
        pk=pk,
        owner=request.user,
        status=ImportBatch.Status.IMPORTED,
    )
    undo_batch(batch)
    messages.info(
        request,
        f"Removed {batch.imported_count} transaction"
        f"{pluralize(batch.imported_count)} created by this import.",
    )
    return redirect("ingestion:batch_detail", pk=batch.pk)
